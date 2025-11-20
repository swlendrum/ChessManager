#include <Arduino.h>

void setup() {
  Serial.begin(115200);
}

void loop() {
  if (Serial.available()) {
    String msg = Serial.readStringUntil('\n');
    if (msg == "ping") {
      Serial.println("pong");
    } else {
      Serial.print("echo: ");
      Serial.println(msg);
    }
  }
}
