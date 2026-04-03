from __future__ import annotations
from dataclasses import dataclass
from typing import Any

import requests


DISCOVERY_URL = "https://discovery.meethue.com/"
APP_NAME = "philips_hue_for_arch#arch_linux"
TIMEOUT = 5


class HueError(RuntimeError):
    """Raised when the Hue bridge returns an error."""


class HueUnauthorizedError(HueError):
    """Raised when the saved Hue bridge username is invalid."""


class HueLinkButtonNotPressedError(HueError):
    """Raised when the bridge link button has not been pressed yet."""


@dataclass
class Bridge:
    bridge_id: str
    ip_address: str


@dataclass
class Light:
    light_id: str
    name: str
    is_on: bool
    brightness: int
    reachable: bool
    supports_color: bool
    supports_temperature: bool
    xy: tuple[float, float] | None
    color_temperature: int | None
    temperature_min: int | None
    temperature_max: int | None


class HueBridgeClient:
    def __init__(self, bridge_ip: str = "", username: str = "") -> None:
        self.bridge_ip = bridge_ip.strip()
        self.username = username.strip()

    def is_configured(self) -> bool:
        return bool(self.bridge_ip and self.username)

    def discover_bridges(self) -> list[Bridge]:
        payload = self._request_json("get", DISCOVERY_URL)
        if not payload:
            raise HueError("No Hue Bridge found on the network.")
        bridges: list[Bridge] = []
        for item in payload:
            bridges.append(
                Bridge(
                    bridge_id=item.get("id", "unknown"),
                    ip_address=item["internalipaddress"],
                )
            )
        return bridges

    def create_user(self) -> str:
        self._ensure_bridge_ip()
        payload = self._request_json(
            "post",
            self._api_base_url(),
            json={"devicetype": APP_NAME},
        )
        return self._extract_success_value(payload, "username")

    def list_lights(self) -> list[Light]:
        self._ensure_configured()
        data = self._decode_hue_payload(self._request_json("get", f"{self._user_api_base()}/lights"))

        lights: list[Light] = []
        for light_id, raw in data.items():
            state = raw.get("state", {})
            model = raw.get("capabilities", {}).get("control", {})
            xy = tuple(state.get("xy", [])) if len(state.get("xy", [])) == 2 else None
            ct_capability = model.get("ct")
            lights.append(
                Light(
                    light_id=light_id,
                    name=raw.get("name", f"Light {light_id}"),
                    is_on=bool(state.get("on", False)),
                    brightness=int(round((state.get("bri", 1) / 254) * 100)),
                    reachable=bool(state.get("reachable", True)),
                    supports_color=bool(model.get("colorgamut")),
                    supports_temperature=bool(ct_capability),
                    xy=xy,
                    color_temperature=state.get("ct"),
                    temperature_min=ct_capability.get("min") if isinstance(ct_capability, dict) else None,
                    temperature_max=ct_capability.get("max") if isinstance(ct_capability, dict) else None,
                )
            )

        return sorted(lights, key=lambda light: light.name.lower())

    def set_power(self, light_id: str, is_on: bool) -> None:
        self._set_state(light_id, {"on": is_on})

    def set_brightness(self, light_id: str, brightness: int) -> None:
        hue_brightness = max(1, min(254, round((brightness / 100) * 254)))
        self._set_state(light_id, {"on": True, "bri": hue_brightness})

    def set_color_rgb(self, light_id: str, red: int, green: int, blue: int) -> None:
        x_value, y_value = self.rgb_to_xy(red, green, blue)
        self._set_state(light_id, {"on": True, "xy": [x_value, y_value]})

    def set_color_temperature(self, light_id: str, mirek: int) -> None:
        self._set_state(light_id, {"on": True, "ct": mirek})

    def _set_state(self, light_id: str, payload: dict[str, Any]) -> None:
        self._ensure_configured()
        response_payload = self._request_json(
            "put",
            f"{self._user_api_base()}/lights/{light_id}/state",
            json=payload,
        )
        self._decode_hue_payload(response_payload)

    def _api_base_url(self) -> str:
        return f"http://{self.bridge_ip}/api"

    def _user_api_base(self) -> str:
        return f"{self._api_base_url()}/{self.username}"

    def _ensure_bridge_ip(self) -> None:
        if not self.bridge_ip:
            raise HueError("No Hue Bridge IP configured.")

    def _ensure_configured(self) -> None:
        if not self.is_configured():
            raise HueError("Hue Bridge is not paired yet.")

    def _request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.Timeout as exc:
            raise HueError("The Hue Bridge took too long to respond.") from exc
        except requests.ConnectionError as exc:
            raise HueError("Could not reach the Hue Bridge. Check the IP and your network.") from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise HueError(f"Hue Bridge request failed with HTTP {status_code}.") from exc
        except requests.RequestException as exc:
            raise HueError(f"Unexpected network error: {exc}") from exc
        except ValueError as exc:
            raise HueError("Hue Bridge returned an invalid response.") from exc

    def _decode_hue_payload(self, payload: Any) -> Any:
        if isinstance(payload, list):
            if payload and isinstance(payload[0], dict):
                if "error" in payload[0]:
                    description = payload[0]["error"].get("description", "Unknown Hue error.")
                    error_type = payload[0]["error"].get("type")
                    if error_type == 1 or "unauthorized user" in description.lower():
                        raise HueUnauthorizedError(description)
                    if error_type == 101 or "link button not pressed" in description.lower():
                        raise HueLinkButtonNotPressedError(description)
                    raise HueError(description)
                if "success" in payload[0] and len(payload) == 1:
                    return payload
        return payload

    def _extract_success_value(self, payload: list[dict[str, Any]], field: str) -> str:
        if not payload:
            raise HueError("Empty response from Hue Bridge.")

        for item in payload:
            if "error" in item:
                description = item["error"].get("description", "Bridge pairing failed.")
                error_type = item["error"].get("type")
                if error_type == 101 or "link button not pressed" in description.lower():
                    raise HueLinkButtonNotPressedError(description)
                continue

            success = item.get("success", {})
            if field in success:
                return str(success[field])

            for key, value in success.items():
                if key.endswith(f"/{field}"):
                    return str(value)

            if len(success) == 1:
                only_value = next(iter(success.values()))
                if isinstance(only_value, str) and only_value:
                    return only_value

        raise HueError("Hue Bridge did not return a username.")

    @staticmethod
    def rgb_to_xy(red: int, green: int, blue: int) -> tuple[float, float]:
        def gamma_correct(channel: float) -> float:
            if channel > 0.04045:
                return ((channel + 0.055) / 1.055) ** 2.4
            return channel / 12.92

        r = gamma_correct(red / 255.0)
        g = gamma_correct(green / 255.0)
        b = gamma_correct(blue / 255.0)

        x = r * 0.664511 + g * 0.154324 + b * 0.162028
        y = r * 0.283881 + g * 0.668433 + b * 0.047685
        z = r * 0.000088 + g * 0.07231 + b * 0.986039

        total = x + y + z
        if total == 0:
            return 0.0, 0.0

        return x / total, y / total

    @staticmethod
    def xy_to_rgb(x_value: float, y_value: float, brightness: int) -> tuple[int, int, int]:
        if y_value == 0:
            return 255, 255, 255

        z_value = 1.0 - x_value - y_value
        y_luma = max(0.01, brightness / 100)
        x_luma = (y_luma / y_value) * x_value
        z_luma = (y_luma / y_value) * z_value

        red = x_luma * 1.656492 - y_luma * 0.354851 - z_luma * 0.255038
        green = -x_luma * 0.707196 + y_luma * 1.655397 + z_luma * 0.036152
        blue = x_luma * 0.051713 - y_luma * 0.121364 + z_luma * 1.01153

        red, green, blue = [max(0.0, value) for value in (red, green, blue)]
        max_channel = max(red, green, blue)
        if max_channel > 1.0:
            red, green, blue = [value / max_channel for value in (red, green, blue)]

        def reverse_gamma(channel: float) -> float:
            if channel <= 0.0031308:
                return 12.92 * channel
            return 1.055 * (channel ** (1 / 2.4)) - 0.055

        red, green, blue = [reverse_gamma(value) for value in (red, green, blue)]
        return tuple(max(0, min(255, round(value * 255))) for value in (red, green, blue))

    @staticmethod
    def mirek_to_kelvin(mirek: int | None) -> int:
        if not mirek:
            return 4000
        return round(1_000_000 / mirek)

    @staticmethod
    def kelvin_to_mirek(kelvin: int) -> int:
        return round(1_000_000 / max(1000, kelvin))
