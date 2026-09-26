/*
 * FlagZero -- hardware stub
 * -------------------------
 * Minimal placeholder so /core and /dashboard have a serial endpoint to
 * develop against before the real sensor wiring (MPU-6500, MAX30102, LCD,
 * LEDs, buzzer, button) is soldered up. See ../PINOUT.md for wiring and
 * ../../shared/serial_protocol.md for the full protocol this implements.
 *
 * What it does:
 *   - Reads one line at a time from Serial (newline-terminated).
 *   - Echoes it back prefixed with "ECHO:" so you can confirm the laptop
 *     side is sending/receiving correctly.
 *   - Every ~200ms, sends a fake telemetry JSON line matching the
 *     UNO -> Laptop shape, using {"vitals":"SIM"} for the pulse-ox fields
 *     since MAX30102 isn't wired into this stub.
 *
 * Replace the TODOs with real sensor reads once hardware is wired. Keep an
 * eye on RAM: the UNO only has 2KB, so this stub deliberately avoids the
 * Arduino String class in the hot path (fixed char buffer + F() macro for
 * flash-resident string literals instead).
 */

#define BAUD 115200
#define LINE_BUF_LEN 40         // plenty for "W,4,T4,9999" + headroom; keep short, RAM is tight
#define TELEMETRY_INTERVAL_MS 200

char lineBuf[LINE_BUF_LEN];
uint8_t lineLen = 0;
unsigned long lastTelemetryMs = 0;

void setup() {
  Serial.begin(BAUD);
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  readSerialLine();

  unsigned long now = millis();
  if (now - lastTelemetryMs >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryMs = now;
    sendFakeTelemetry();
  }
}

void readSerialLine() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen > 0) {
        lineBuf[lineLen] = '\0';
        handleCommand(lineBuf);
        lineLen = 0;
      }
    } else if (lineLen < LINE_BUF_LEN - 1) {
      lineBuf[lineLen++] = c;
    }
    // silently drop overflow chars rather than growing the buffer -- keeps
    // RAM bounded no matter what the laptop sends
  }
}

void handleCommand(const char *line) {
  // Stub behaviour: just echo. Once wired up, parse per
  // ../../shared/serial_protocol.md (commands start with 'W', 'C', or 'I')
  // and drive LEDs/buzzer/LCD here instead.
  digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));  // blink to show life
  Serial.print(F("ECHO:"));
  Serial.println(line);

  // TODO: real parsing, e.g.
  //   if (line[0] == 'W') { ... parse level, location, distance_m ... }
  //   if (line[0] == 'C') { ... start/stop countdown ... }
  //   if (line[0] == 'I') { ... reset to idle ... }
}

void sendFakeTelemetry() {
  // Fixed-format print instead of building a String, to keep RAM flat.
  Serial.print(F("{\"g_force\":1.0,\"yaw_rate\":0,\"still_ms\":"));
  Serial.print(millis());
  Serial.print(F(",\"button\":0,\"vitals\":\"SIM\"}"));
  Serial.println();
}
