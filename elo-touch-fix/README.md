# Elo touchscreen fix (Elo 2216 AccuTouch / TS15R) for Debian

Fixes a touchscreen that is flipped and/or offset. Works on X11 and Wayland.

## Install (one command)

Open a terminal and paste:

    wget -qO- https://raw.githubusercontent.com/adman234/-other/elo-touch-fix/elo-touch-fix/install.sh | sh

## Calibrate

Double-click **Calibrate Touchscreen** on the desktop (or find it in the app
menu), then touch the 5 red crosses. Done - it is saved permanently.

On Wayland you are asked for your password once.

## Other commands

    elo-touch-fix flip      # just flip the Y axis
    elo-touch-fix status    # show current calibration
    elo-touch-fix reset     # remove calibration
