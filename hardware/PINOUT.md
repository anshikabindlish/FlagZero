# Pinout — Arduino UNO

Assumes a **1602A LCD on an I2C backpack** (2-wire), not a parallel
HD44780 (6+ digital pins) — that's what's in most starter kits. **If yours
is the parallel version, flag it** and the pin map + sketch's LCD calls
need to change.

| Component | Interface | UNO pins | Notes |
|---|---|---|---|
| MPU-6500 (IMU) | I2C | A4 (SDA), A5 (SCL) | address `0x68` |
| MAX30102 (pulse-ox) | I2C | A4 (SDA), A5 (SCL) | address `0x57`, shares the bus with the IMU |
| 1602A LCD (I2C backpack) | I2C | A4 (SDA), A5 (SCL) | address `0x27` (some backpacks use `0x3F` — check yours) |
| LED green (clear) | digital out | D2 | |
| LED yellow (caution) | digital out | D3 | |
| LED red (danger) | digital out | D4 | |
| Buzzer | digital out (or PWM) | D5 | |
| "I'm OK" button | digital in, pull-up | D6 | wire to GND, use `INPUT_PULLUP`, active-low |

All three I2C devices share SDA/SCL, which is fine as long as each has a
distinct address. If your LCD backpack happens to collide with `0x68` or
`0x57` (rare, but check), run an I2C scanner sketch before wiring day-of to
confirm addresses.
