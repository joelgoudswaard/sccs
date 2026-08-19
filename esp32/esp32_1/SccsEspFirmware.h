/*
 * SCCS ESP32-S3 lighting / analog MCU firmware (shared core).
 *
 * Host link: UART0 on GPIO 44 (RX / U0RXD) / 43 (TX / U0TXD) to the Raspberry Pi, 115200 8N1.
 * Those are the same pins the ROM bootloader uses, so BOOT+RESET flashes over the Pi UART.
 *
 * Protocol:
 *   SET <gpio> <pwm0-255>
 *   RAMP <gpio> <pwm0-255> <duration_ms>
 *   GET <gpio>              → VALUE <gpio> <pwm>
 *   ANALOG <gpio>           → ANALOG <gpio> <avg_counts>
 *   GETVCC                  → VCC <millivolts>
 *   GETALL                  → VALUES <gpio>:<pwm> ...
 *
 * Include from a board sketch after defining:
 *   SCCS_ESP_ID            1 or 2
 *   SCCS_PWM_PINS          {4,5,6,7,8,9,10,11}
 *   SCCS_ANALOG_PINS       {1} or {1,2}
 * Optional: SCCS_HOST_RX / SCCS_HOST_TX (defaults 44 / 43, UART0)
 */

#pragma once

#ifndef SCCS_ESP_ID
#error "Define SCCS_ESP_ID (1 or 2) before including SccsEspFirmware.h"
#endif

#ifndef SCCS_PWM_PINS
#error "Define SCCS_PWM_PINS before including SccsEspFirmware.h"
#endif

#ifndef SCCS_ANALOG_PINS
#error "Define SCCS_ANALOG_PINS before including SccsEspFirmware.h"
#endif

#ifndef SCCS_BAUD
#define SCCS_BAUD 115200
#endif

// Pi ↔ ESP UART0 (both ESP32-1 and ESP32-2 on the SCCS PCB).
// Matches U0RXD/U0TXD on the WROOM-1U — same UART the ROM bootloader uses.
#ifndef SCCS_HOST_RX
#define SCCS_HOST_RX 44
#endif
#ifndef SCCS_HOST_TX
#define SCCS_HOST_TX 43
#endif

#ifndef SCCS_PWM_FREQ_HZ
#define SCCS_PWM_FREQ_HZ 5000
#endif

#ifndef SCCS_PWM_RES_BITS
#define SCCS_PWM_RES_BITS 8
#endif

#ifndef SCCS_ADC_SAMPLES
#define SCCS_ADC_SAMPLES 64
#endif

// 3V3 rail report for water-level math on the Pi (mV).
#ifndef SCCS_VCC_MV
#define SCCS_VCC_MV 3300
#endif

#include <Arduino.h>

static const int kPwmPins[] = SCCS_PWM_PINS;
static const int kNumPwm = sizeof(kPwmPins) / sizeof(kPwmPins[0]);
static const int kAnalogPins[] = SCCS_ANALOG_PINS;
static const int kNumAnalog = sizeof(kAnalogPins) / sizeof(kAnalogPins[0]);

static const int kMaxGpio = 48;

struct PwmState {
  int current_value;
  int start_value;
  int target;
  unsigned long start_time;
  unsigned long duration;
  bool active;
};

static PwmState g_pwm[kMaxGpio];
static bool g_is_pwm[kMaxGpio];
static bool g_is_analog[kMaxGpio];

// UART0 is already Serial0 in the Arduino-ESP32 core. A second
// HardwareSerial(0) does not share that driver and stays silent on the Pi.
#define HostSerial Serial0

static char g_line[96];
static size_t g_line_len = 0;

static bool isPwmGpio(int gpio) {
  return gpio >= 0 && gpio < kMaxGpio && g_is_pwm[gpio];
}

static bool isAnalogGpio(int gpio) {
  return gpio >= 0 && gpio < kMaxGpio && g_is_analog[gpio];
}

static void processSet(const String &args) {
  int sp = args.indexOf(' ');
  if (sp < 0) return;
  int gpio = args.substring(0, sp).toInt();
  int value = args.substring(sp + 1).toInt();
  if (!isPwmGpio(gpio) || value < 0 || value > 255) return;
  g_pwm[gpio].current_value = value;
  g_pwm[gpio].target = value;
  g_pwm[gpio].duration = 0;
  g_pwm[gpio].active = false;
  analogWrite(gpio, value);
}

static void processRamp(const String &args) {
  int s1 = args.indexOf(' ');
  int s2 = args.indexOf(' ', s1 + 1);
  if (s1 < 0 || s2 < 0) return;
  int gpio = args.substring(0, s1).toInt();
  int target = args.substring(s1 + 1, s2).toInt();
  unsigned long duration = args.substring(s2 + 1).toInt();
  if (!isPwmGpio(gpio) || target < 0 || target > 255 || duration == 0) return;
  g_pwm[gpio].start_value = g_pwm[gpio].current_value;
  g_pwm[gpio].target = target;
  g_pwm[gpio].start_time = millis();
  g_pwm[gpio].duration = duration;
  g_pwm[gpio].active = true;
}

static void processGet(const String &args) {
  int gpio = args.toInt();
  if (!isPwmGpio(gpio)) return;
  HostSerial.print("VALUE ");
  HostSerial.print(gpio);
  HostSerial.print(' ');
  HostSerial.println(g_pwm[gpio].current_value);
}

static float readAnalogAvg(int gpio) {
  long sum = 0;
  for (int i = 0; i < SCCS_ADC_SAMPLES; i++) {
    sum += analogRead(gpio);
    delayMicroseconds(50);
  }
  return static_cast<float>(sum) / static_cast<float>(SCCS_ADC_SAMPLES);
}

static void processAnalog(const String &args) {
  int gpio = args.toInt();
  if (!isAnalogGpio(gpio)) return;
  float value = readAnalogAvg(gpio);
  HostSerial.print("ANALOG ");
  HostSerial.print(gpio);
  HostSerial.print(' ');
  HostSerial.println(value, 3);
}

static void processGetVcc() {
  HostSerial.print("VCC ");
  HostSerial.println(SCCS_VCC_MV);
}

static void processGetAll() {
  HostSerial.print("VALUES");
  for (int i = 0; i < kNumPwm; i++) {
    int gpio = kPwmPins[i];
    HostSerial.print(' ');
    HostSerial.print(gpio);
    HostSerial.print(':');
    HostSerial.print(g_pwm[gpio].current_value);
  }
  HostSerial.println();
}

static void servicePwmRamps() {
  static unsigned long last_update = 0;
  unsigned long now = millis();
  if (now - last_update < 4) return;
  last_update = now;

  for (int i = 0; i < kNumPwm; i++) {
    int gpio = kPwmPins[i];
    PwmState &st = g_pwm[gpio];
    if (!st.active || st.duration == 0) continue;

    unsigned long elapsed = now - st.start_time;
    if (elapsed >= st.duration) {
      st.current_value = st.target;
      st.duration = 0;
      st.active = false;
      analogWrite(gpio, st.current_value);
    } else {
      float progress = static_cast<float>(elapsed) / static_cast<float>(st.duration);
      int value = st.start_value +
                  static_cast<int>((st.target - st.start_value) * progress);
      st.current_value = value;
      analogWrite(gpio, value);
    }
  }
}

static void handleLine(String command) {
  command.trim();
  if (command.length() == 0) return;

  if (command.startsWith("SET ")) {
    processSet(command.substring(4));
  } else if (command.startsWith("RAMP ")) {
    processRamp(command.substring(5));
  } else if (command.startsWith("GET ")) {
    processGet(command.substring(4));
  } else if (command.startsWith("ANALOG ")) {
    processAnalog(command.substring(7));
  } else if (command.startsWith("GETVCC")) {
    processGetVcc();
  } else if (command == "GETALL" || command.startsWith("GETALL")) {
    processGetAll();
  }
}

static void serviceHostSerial() {
  while (HostSerial.available() > 0) {
    const int raw = HostSerial.read();
    if (raw < 0) {
      break;
    }
    const char c = static_cast<char>(raw);
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      g_line[g_line_len] = '\0';
      handleLine(String(g_line));
      g_line_len = 0;
      continue;
    }
    if (g_line_len + 1 < sizeof(g_line)) {
      g_line[g_line_len++] = c;
    } else {
      g_line_len = 0;
    }
  }
}

static void sccsSetup() {
  // Bring up the host UART before PWM/ADC so a later init hang is still visible.
  HostSerial.setTxBufferSize(256);
  HostSerial.setRxBufferSize(256);
  HostSerial.begin(SCCS_BAUD, SERIAL_8N1, SCCS_HOST_RX, SCCS_HOST_TX);
  HostSerial.setDebugOutput(false);
  HostSerial.setTimeout(20);
  delay(50);
  HostSerial.print("SCCS ");
  HostSerial.print(SCCS_ESP_ID);
  HostSerial.println(" READY");

  memset(g_pwm, 0, sizeof(g_pwm));
  memset(g_is_pwm, 0, sizeof(g_is_pwm));
  memset(g_is_analog, 0, sizeof(g_is_analog));

  for (int i = 0; i < kNumPwm; i++) {
    int gpio = kPwmPins[i];
    if (gpio < 0 || gpio >= kMaxGpio) continue;
    g_is_pwm[gpio] = true;
    pinMode(gpio, OUTPUT);
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    analogWriteResolution(gpio, SCCS_PWM_RES_BITS);
    analogWriteFrequency(gpio, SCCS_PWM_FREQ_HZ);
#else
    analogWriteResolution(SCCS_PWM_RES_BITS);
#endif
    g_pwm[gpio].current_value = 0;
    g_pwm[gpio].target = 0;
    g_pwm[gpio].duration = 0;
    g_pwm[gpio].active = false;
    analogWrite(gpio, 0);
  }

  analogReadResolution(12);
  for (int i = 0; i < kNumAnalog; i++) {
    int gpio = kAnalogPins[i];
    if (gpio < 0 || gpio >= kMaxGpio) continue;
    g_is_analog[gpio] = true;
    pinMode(gpio, INPUT);
    analogSetPinAttenuation(gpio, ADC_11db);
  }
}

static void sccsLoop() {
  servicePwmRamps();
  serviceHostSerial();
}
