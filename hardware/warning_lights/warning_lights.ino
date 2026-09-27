/*
  FlagZero in-car warning lights -- Arduino UNO. Runs with the laptop script:
      py -m flagzero.tools.warning_lights            (follows car 21, the car behind the crash)
  Setup and wiring: hardware/warning_lights/README.md

  Buzzer (active) A1, yellow LED A2, red LED A3 (each LED through 220 ohm). No buttons.

  Laptop -> Arduino, a few times a second:  "S <level>"
      0 clear, 1 caution, 2 yellow, 3 double yellow, 4 slow zone, 5 red
  Arduino -> laptop: "READY" at start.
*/
const byte BUZZER = A1, LED_YELLOW = A2, LED_RED = A3;
const byte BEEPS[6] = {0, 1, 2, 3, 3, 6};        // same as the phone
const unsigned long LINK_TIMEOUT_MS = 3000;

int level = 0;
unsigned long lastMsgMs = 0;
bool everLinked = false;

int beepsLeft = 0;
bool buzzerOn = false;
unsigned long buzzerNextMs = 0;

char line[16];
byte lineLen = 0;

void beep(int n) {
  beepsLeft = n;
  buzzerOn = false;
  digitalWrite(BUZZER, LOW);
  buzzerNextMs = millis();
}

void updateBuzzer(unsigned long now) {
  if (beepsLeft <= 0 || now < buzzerNextMs) return;
  buzzerOn = !buzzerOn;
  digitalWrite(BUZZER, buzzerOn ? HIGH : LOW);
  if (!buzzerOn) beepsLeft--;
  buzzerNextMs = now + 110;
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      line[lineLen] = '\0';
      if (lineLen >= 3 && line[0] == 'S') {
        int lv = atoi(line + 1);
        if (lv >= 0 && lv <= 5) {
          if (lv > level) beep(BEEPS[lv]);                        // the flag got worse
          level = lv;
          lastMsgMs = millis();
          everLinked = true;
        }
      }
      lineLen = 0;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}

void updateLeds(unsigned long now, bool linked) {
  bool phase = (now / 250) % 2 == 0;
  bool y = false, r = false;
  if (!linked) {
    y = (now % 1000) < 100;                                       // short yellow blip: waiting for the laptop
  } else if (level == 5) {
    r = true;                                                     // red flag
  } else if (level >= 3) {
    y = phase;                                                    // double yellow / slow zone: flashing
  } else if (level >= 1) {
    y = true;                                                     // caution / yellow
  }
  digitalWrite(LED_YELLOW, y);
  digitalWrite(LED_RED, r);
}

void setup() {
  Serial.begin(9600);
  pinMode(BUZZER, OUTPUT);
  pinMode(LED_YELLOW, OUTPUT);
  pinMode(LED_RED, OUTPUT);

  digitalWrite(LED_YELLOW, HIGH);   // self-test: both LEDs + one beep
  digitalWrite(LED_RED, HIGH);
  digitalWrite(BUZZER, HIGH);
  delay(200);
  digitalWrite(BUZZER, LOW);
  delay(300);
  digitalWrite(LED_YELLOW, LOW);
  digitalWrite(LED_RED, LOW);
  Serial.println("READY");
}

void loop() {
  readSerial();
  unsigned long now = millis();
  bool linked = everLinked && now - lastMsgMs < LINK_TIMEOUT_MS;
  updateLeds(now, linked);
  updateBuzzer(now);
}

