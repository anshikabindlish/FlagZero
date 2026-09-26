# /hardware — Person C (part 1)

Owns: the Arduino UNO sketch — reading the MPU-6500 + MAX30102 + button,
and driving the LCD + LEDs + buzzer from commands sent over serial.

## Stub: `flagzero_stub/flagzero_stub.ino`

Open `flagzero_stub/flagzero_stub.ino` in the Arduino IDE (the sketch
folder name must match the `.ino` filename — that's an Arduino IDE
requirement, already set up here), select **Arduino Uno** as the board,
and upload.

At 115200 baud, it:

- echoes back (prefixed `ECHO:`) any line you send it, so you can confirm
  the laptop→UNO link works before wiring real commands to real hardware
- blinks the built-in LED on every received line, as a visual heartbeat
- sends a fake telemetry JSON line (`{"vitals":"SIM", ...}`) every ~200ms,
  matching the shape in `shared/serial_protocol.md`

Test it with the Arduino IDE's Serial Monitor (set line ending to
"Newline"): type `W,2,T4,210` and confirm you see `ECHO:W,2,T4,210` plus
the periodic telemetry lines.

## Wiring

See `PINOUT.md` for the suggested pin/I2C-address map for the IMU,
pulse-oximeter, LCD, 3 LEDs, buzzer, and button.

## Replacing the stub

Parse `lineBuf` in `handleCommand()` per the grammar in
`../shared/serial_protocol.md` (`W,...` / `C,START,...` / `C,STOP` / `I`)
and drive the LEDs/buzzer/LCD instead of just echoing. Replace
`sendFakeTelemetry()` with real MPU-6500 + MAX30102 + button reads once
they're wired — keep the JSON shape identical so `/core` doesn't need to
change.

Remember: this sketch never needs to know `car_id` — see the note at the
bottom of `../shared/serial_protocol.md`.
