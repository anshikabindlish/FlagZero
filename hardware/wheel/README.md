# FlagZero steering wheel (Arduino, no soldering)

A cardboard F1-style wheel: physical buttons, a yellow and a red flag LED and a buzzer on
a breadboard, with a phone taped in the middle as the display. The buttons do exactly what
the phone's own buttons do. The iPhone (car 17) stays the crash sensor.

## Wiring (Arduino UNO)

| Part | Arduino pin | Other side |
|---|---|---|
| I'M OK button | D2 | GND (blue line) |
| CONTINUE UNDER YELLOW button | D3 | GND |
| RECOMMEND RED FLAG button | D4 | GND |
| REPORT FALSE ALARM button | D5 | GND |
| TEST CRASH button (optional) | A0 | GND |
| Buzzer (active) + leg | A1 | − leg to GND |
| Yellow LED | A2 → 220 Ω → long leg | short leg to GND |
| Red LED | A3 → 220 Ω → long leg | short leg to GND |
| 16x2 LCD (optional; it wasn't reliable on our board) | RS 12, E 11, D4 10, D5 9, D6 8, D7 7 | VO, RW, GND, BLK → GND; VDD → 5V; BLA → 220 Ω → 5V |

Buttons: pin wire and GND wire in **different** rows, nothing else in those rows, and keep
them away from the LCD's rows. If a button reads as always pressed, turn it 90°.

## Run

1. Arduino IDE: open `hardware/wheel/wheel.ino`, choose Board **Arduino Uno** and the port, click Upload.
   Both LEDs flash and it beeps once. Then **close the Arduino IDE** (it keeps the USB port busy).
2. Once: `py -m pip install pyserial`
3. Start the demo as usual (`start_demo.bat`).
4. On the laptop the Arduino is plugged into: `py -m flagzero.tools.wheel`
   (server on another laptop: add `--url wss://<tunnel>.trycloudflare.com`).
5. On the phone taped to the wheel, open `https://<tunnel>/car?car=17&wheel=1` and tap START (for sound).

## What it does

| Situation | Wheel LEDs / buzzer | Buttons that work |
|---|---|---|
| Track clear | off | – |
| Caution / yellow | yellow on | – |
| Double yellow / slow zone | yellow flashing | – |
| Red flag | red on | – |
| I'M OK countdown | red and yellow alternate, beep every second | I'M OK, RED FLAG |
| After I'M OK | as the flag | CONTINUE, FALSE ALARM, RED FLAG |
| No link to the laptop | short yellow blip every second | – |

The wheel script also takes keys (type + Enter): o I'M OK, c continue, r red flag,
f false alarm, t test crash. `py -m flagzero.tools.wheel --no-arduino` works without the Arduino.
