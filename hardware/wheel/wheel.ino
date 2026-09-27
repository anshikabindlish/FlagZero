/*
  FlagZero steering wheel -- Arduino UNO. Runs with the laptop script:
      py -m flagzero.tools.wheel
  Setup and wiring: hardware/wheel/README.md

  Buttons (other leg to GND): I'M OK D2, CONTINUE D3, RED FLAG D4, FALSE ALARM D5, TEST CRASH A0
  Buzzer (active) A1, yellow LED A2, red LED A3 (each LED through 220 ohm).
  Optional 16x2 LCD: RS 12, E 11, D4 10, D5 9, D6 8, D7 7 (the phone on the wheel is the main display).

  Laptop -> wheel, a few times a second:  "S <level> <countdown>"
      level 0 clear, 1 caution, 2 yellow, 3 double yellow, 4 slow zone, 5 red
      countdown = seconds left on the I'M OK check, or -1 when there is none
  Wheel -> laptop:  "READY" at start, "BTN OK|CONT|RED|FALSE|TEST" when a button is pressed.
*/
#include <LiquidCrystal.h>

LiquidCrystal lcd(12, 11, 10, 9, 8, 7);
const byte BTN_PINS[] = {2, 3, 4, 5, A0};
const char* BTN_NAMES[] = {"OK", "CONT", "RED", "FALSE", "TEST"};
const byte NUM_BTNS = 5;
const byte BUZZER = A1, LED_YELLOW = A2, LED_RED = A3;
const byte BEEPS[6] = {0, 1, 2, 3, 3, 6};        // same as the phone
const char* LABELS[6] = {"TRACK CLEAR", "CAUTION", "YELLOW", "DOUBLE YELLOW", "SLOW ZONE", "RED FLAG"};
const unsigned long LINK_TIMEOUT_MS = 3000;

int level = 0, countdown = -1;
unsigned long lastMsgMs = 0;
bool everLinked = false;

bool btnState[NUM_BTNS];
unsigned long btnChangedMs[NUM_BTNS];

int beepsLeft = 0;
bool buzzerOn = false;
unsigned long buzzerNextMs = 0, beepLenMs = 110;

char line[24];
byte lineLen = 0;
char shown1[17] = "", shown2[17] = "";
unsigned long lcdDrawnMs = 0;

void beep(int n, unsigned long lenMs = 110) {
  beepsLeft = n;
  beepLenMs = lenMs;
  buzzerOn = false;
  digitalWrite(BUZZER, LOW);
  buzzerNextMs = millis();
}

void updateBuzzer(unsigned long now) {
  if (beepsLeft <= 0 || now < buzzerNextMs) return;
  buzzerOn = !buzzerOn;
  digitalWrite(BUZZER, buzzerOn ? HIGH : LOW);
  if (!buzzerOn) beepsLeft--;
  buzzerNextMs = now + beepLenMs;
}

void onState(int newLevel, int newCountdown) {
  if (newCountdown >= 0 && newCountdown != countdown) beep(1, 150);          // countdown tick
  else if (newCountdown < 0 && newLevel > level) beep(BEEPS[newLevel]);       // flag got worse
  level = newLevel;
  countdown = newCountdown;
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      line[lineLen] = '\0';
      if (lineLen > 0 && line[0] == 'S') {
        int lv = 0, cd = -1;
        char* tok = strtok(line + 1, " ");
        if (tok) { lv = atoi(tok); tok = strtok(NULL, " "); }
        if (tok) cd = atoi(tok);
        if (lv >= 0 && lv <= 5) {
          onState(lv, cd);
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

void checkButtons(unsigned long now) {
  for (byte i = 0; i < NUM_BTNS; i++) {
    bool reading = digitalRead(BTN_PINS[i]);
    if (reading != btnState[i] && now - btnChangedMs[i] > 50) {   // 50 ms debounce
      btnState[i] = reading;
      btnChangedMs[i] = now;
      if (reading == LOW) {                                       // pressed
        Serial.print("BTN ");
        Serial.println(BTN_NAMES[i]);
        beep(1, 40);                                              // click
      }
    }
  }
}

void updateLeds(unsigned long now, bool linked) {
  bool phase = (now / 250) % 2 == 0;
  bool y = false, r = false;
  if (!linked) {
    y = (now / 1000) % 2 == 0 && (now % 1000) < 100;                // short yellow blip: waiting for the laptop
  } else if (countdown >= 0) {
    r = phase; y = !phase;                                        // I'M OK check: red/yellow alternate
  } else if (level == 5) {
    r = true;
  } else if (level >= 3) {
    y = phase;                                                    // double yellow / slow zone: flashing
  } else if (level >= 1) {
    y = true;
  }
  digitalWrite(LED_YELLOW, y);
  digitalWrite(LED_RED, r);
}

void updateLcd(unsigned long now, bool linked) {                  // optional: harmless if no LCD works
  char l1[17], l2[17];
  if (!linked) {
    strcpy(l1, "FLAGZERO WHEEL");
    strcpy(l2, everLinked ? "NO LINK" : "WAITING...");
  } else if (countdown >= 0) {
    strcpy(l1, "IMPACT! YOU OK?");
    snprintf(l2, sizeof(l2), "PRESS OK    %2ds", countdown);
  } else {
    strcpy(l1, LABELS[level]);
    strcpy(l2, level == 5 ? "STOP" : "");
  }
  bool changed = strcmp(l1, shown1) != 0 || strcmp(l2, shown2) != 0;
  if (!changed && now - lcdDrawnMs < 3000) return;
  lcd.begin(16, 2);                 // restart the LCD every time: it sometimes misses one start-up
  lcd.clear();
  lcd.print(l1);
  lcd.setCursor(0, 1);
  lcd.print(l2);
  strcpy(shown1, l1);
  strcpy(shown2, l2);
  lcdDrawnMs = now;
}

void setup() {
  Serial.begin(9600);
  for (byte i = 0; i < NUM_BTNS; i++) {
    pinMode(BTN_PINS[i], INPUT_PULLUP);
    btnState[i] = HIGH;
    btnChangedMs[i] = 0;
  }
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
  checkButtons(now);
  updateLeds(now, linked);
  updateBuzzer(now);
  updateLcd(now, linked);
}
