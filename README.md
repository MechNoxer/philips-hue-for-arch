# Hue Remote

Simple Linux desktop app for controlling Philips Hue lights with a clean GUI.

## Features

- Discover your Hue bridge on the local network
- Pair with the bridge from the desktop app
- Turn lights on and off
- Adjust brightness
- Pick colors for color-capable lamps
- Refresh lamp state from the bridge

## Run

For `bash`/`sh`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
hue-remote
```

For `fish`:

```fish
python3 -m venv .venv
source .venv/bin/activate.fish
python -m ensurepip --upgrade
pip install -e .
hue-remote
```

Without activating the virtual environment:

```bash
python3 -m venv .venv
./.venv/bin/python3 -m ensurepip --upgrade
.venv/bin/pip install -e .
.venv/bin/hue-remote
```

If the environment gets into a broken state, rebuild it:

```fish
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate.fish
python -m ensurepip --upgrade
python -m pip install -e .
hue-remote
```

If you want it in your app launcher, copy [`hue-remote.desktop`](/home/mauricedelauw/Documents/Code projects/philps hue remote/hue-remote.desktop) to `~/.local/share/applications/`.

## Pairing

1. Start the app.
2. Open `Add Hub`.
3. Click `Discover Bridge`.
4. If more than one bridge is found, select the bridge you want to pair.
5. Click `Pair Bridge`.
6. Press the physical button on the selected Hue Bridge while the app is waiting.
7. The app detects the button press automatically, pairs, and returns to the home page.

If you see `unauthorized user`, use `Forget Hub`, then go through the add-hub flow again. The Hue bridge username is a generated token, not your personal account name.

The app stores the paired bridge details in:

`~/.config/hue-remote/config.json`

## Arch / yay

To make this installable with `yay`, publish the code to GitHub, create a `v0.1.0`
tag, then use the files in [`packaging/`](/home/mauricedelauw/Documents/Code projects/philps hue remote/packaging)
for your AUR package repository.

Typical flow:

```bash
git tag v0.1.0
git push origin main --tags
```

Then create a separate AUR repo named `hue-remote`, copy in:

- `packaging/PKGBUILD`
- `packaging/.SRCINFO`

Users can then install it with:

```bash
yay -S hue-remote
```

If you change the GitHub repo name, update the URL in `packaging/PKGBUILD` and regenerate
`packaging/.SRCINFO`.
