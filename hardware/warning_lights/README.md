# FlagZero in-car warning lights (Arduino, no soldering)

A yellow LED, a red LED and a buzzer on a breadboard: the in-car warning light for **car 21, the
car behind the crash**. It shows the same warning as car 21's phone, at the same moment.

## Wiring (Arduino UNO)

| Part | Arduino pin | Other side |
|---|---|---|
| Buzzer (active), long leg (+) | A1 | short leg → GND (blue line) |
| Yellow LED | A2 → 220 Ω resistor → long leg | short leg → GND |
| Red LED | A3 → 220 Ω resistor → long leg | short leg → GND |
| Breadboard blue (−) line | GND | – |

Nothing uses 5V or 3.3V. The resistor's end and the LED's long leg must be in the same row, on
the same side of the breadboard's groove. If an LED doesn't light, it's probably in backwards.

## Run

1. Arduino IDE: open `hardware/warning_lights/warning_lights.ino`, choose Board **Arduino Uno**
   and the port, click **Upload**. Both LEDs flash and it beeps once. Then **close the Arduino IDE**
   (it keeps the USB port busy).
2. Once: `py -m pip install pyserial`
3. Start the demo as usual (`start_demo.bat`).
4. On the laptop the Arduino is plugged into: `py -m flagzero.tools.warning_lights`
   - server on another laptop: add `--url wss://<tunnel>.trycloudflare.com`
   - follow a different car: `--car N` (default 21)

## What it shows

| Car's warning | Lights | Buzzer |
|---|---|---|
| Clear | off | – |
| Caution / yellow | yellow on | 1-2 beeps when it comes on |
| Double yellow / slow zone | yellow flashing | 3 beeps |
| Red flag | red on | 6 beeps |
| No link to the laptop | short yellow blip every second | – |

`py -m flagzero.tools.warning_lights --no-arduino` prints the same warnings without the Arduino.
