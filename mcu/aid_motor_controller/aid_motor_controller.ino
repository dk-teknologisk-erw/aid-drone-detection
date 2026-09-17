const uint8_t DIR_PIN = 1;
const uint8_t STEP_PIN = 0;
const uint8_t ENABLE_PIN = 2;
const float STEPS_PER_REVOLUTION = 120.0f * 16.0f;
const unsigned long STATUS_INTERVAL_MS = 100;
const unsigned long COMMAND_TIMEOUT_MS = 1500;
const unsigned long STEP_PULSE_US = 50;
const float ACCELERATION_DPS2 = 30.0f;
const float MINIMUM_MOTION_SPEED_DPS = 2.0f;

enum MotorMode { STOPPED, EXPLORING, FOCUSING };

MotorMode mode = STOPPED;
float angleDeg = 0.0f;
float targetDeg = 0.0f;
float speedDps = 30.0f;
float currentSpeedDps = 0.0f;
unsigned long lastStepUs = 0;
unsigned long lastMotionUpdateUs = 0;
unsigned long lastStatusMs = 0;
unsigned long lastCommandMs = 0;
String inputLine;
bool driverEnabled = false;

void setDriverEnabled(bool enabled)
{
  if (driverEnabled == enabled) return;
  digitalWrite(ENABLE_PIN, enabled ? LOW : HIGH);
  driverEnabled = enabled;
  if (enabled) delayMicroseconds(1);
}

float normalizeAngle(float angle)
{
  while (angle >= 360.0f) angle -= 360.0f;
  while (angle < 0.0f) angle += 360.0f;
  return angle;
}

float shortestDifference(float target, float current)
{
  float difference = normalizeAngle(target) - normalizeAngle(current);
  if (difference > 180.0f) difference -= 360.0f;
  if (difference < -180.0f) difference += 360.0f;
  return difference;
}

bool readNumber(const String &line, const char *key, float &value)
{
  String marker = String("\"") + key + "\":";
  int start = line.indexOf(marker);
  if (start < 0) return false;
  start += marker.length();
  value = line.substring(start).toFloat();
  return true;
}

bool hasValue(const String &line, const char *key, const char *value)
{
  String field = String("\"") + key + "\":\"" + value + "\"";
  return line.indexOf(field) >= 0;
}

void processCommand(const String &line)
{
  lastCommandMs = millis();
  float value;
  if (hasValue(line, "cmd", "mode")) {
    if (hasValue(line, "mode", "explore")) mode = EXPLORING;
    else if (hasValue(line, "mode", "focus")) mode = FOCUSING;
    else mode = STOPPED;
  } else if (hasValue(line, "cmd", "speed") && readNumber(line, "speed_dps", value)) {
    speedDps = constrain(value, 0.1f, 90.0f);
  } else if (hasValue(line, "cmd", "target") && readNumber(line, "angle_deg", value)) {
    targetDeg = normalizeAngle(value);
  } else if (hasValue(line, "cmd", "zero") && readNumber(line, "angle_deg", value)) {
    angleDeg = normalizeAngle(value);
  } else if (hasValue(line, "cmd", "stop")) {
    mode = STOPPED;
  }
}

void readCommands()
{
  while (Serial.available()) {
    char character = Serial.read();
    if (character == '\n') {
      processCommand(inputLine);
      inputLine = "";
    } else if (character != '\r' && inputLine.length() < 160) {
      inputLine += character;
    }
  }
}

void stepMotor(bool positive)
{
  digitalWrite(DIR_PIN, positive ? HIGH : LOW);
  digitalWrite(STEP_PIN, HIGH);
  delayMicroseconds(STEP_PULSE_US);
  digitalWrite(STEP_PIN, LOW);
  float stepAngle = 360.0f / STEPS_PER_REVOLUTION;
  angleDeg = normalizeAngle(angleDeg + (positive ? stepAngle : -stepAngle));
}

void updateMotor()
{
  unsigned long now = micros();
  float elapsedSeconds = (now - lastMotionUpdateUs) / 1000000.0f;
  lastMotionUpdateUs = now;

  if (mode != STOPPED && millis() - lastCommandMs > COMMAND_TIMEOUT_MS) {
    mode = STOPPED;
  }
  if (mode == STOPPED) {
    currentSpeedDps = 0.0f;
    setDriverEnabled(false);
    return;
  }
  setDriverEnabled(true);

  float desiredSpeedDps = speedDps;
  float difference = 0.0f;
  if (mode == FOCUSING) {
    difference = shortestDifference(targetDeg, angleDeg);
    float stoppingSpeedDps = sqrt(2.0f * ACCELERATION_DPS2 * abs(difference));
    desiredSpeedDps = min(speedDps, stoppingSpeedDps);
    if (abs(difference) <= (180.0f / STEPS_PER_REVOLUTION)) {
      mode = STOPPED;
      currentSpeedDps = 0.0f;
      setDriverEnabled(false);
      return;
    }
  }

  float speedChange = ACCELERATION_DPS2 * elapsedSeconds;
  if (currentSpeedDps < desiredSpeedDps) {
    currentSpeedDps = min(desiredSpeedDps, currentSpeedDps + speedChange);
  } else {
    currentSpeedDps = max(desiredSpeedDps, currentSpeedDps - speedChange);
  }
  float steppingSpeedDps = max(currentSpeedDps, MINIMUM_MOTION_SPEED_DPS);
  float stepIntervalUs = 1000000.0f * 360.0f / (steppingSpeedDps * STEPS_PER_REVOLUTION);
  if ((unsigned long)(now - lastStepUs) < (unsigned long)stepIntervalUs) return;
  lastStepUs = now;

  if (mode == EXPLORING) {
    stepMotor(true);
    return;
  }

  stepMotor(difference > 0.0f);
}

const char *modeName()
{
  if (mode == EXPLORING) return "explore";
  if (mode == FOCUSING) return "focus";
  return "stop";
}

void publishStatus()
{
  unsigned long now = millis();
  if (now - lastStatusMs < STATUS_INTERVAL_MS) return;
  lastStatusMs = now;
  Serial.print("{\"type\":\"status\",\"mode\":\"");
  Serial.print(modeName());
  Serial.print("\",\"angle_deg\":");
  Serial.print(angleDeg, 3);
  Serial.print(",\"target_deg\":");
  Serial.print(targetDeg, 3);
  Serial.print(",\"speed_dps\":");
  Serial.print(speedDps, 3);
  Serial.print(",\"current_speed_dps\":");
  Serial.print(currentSpeedDps, 3);
  Serial.print(",\"moving\":");
  Serial.print(mode == STOPPED ? "false" : "true");
  Serial.print(",\"driver_enabled\":");
  Serial.print(driverEnabled ? "true" : "false");
  Serial.print(",\"command_age_ms\":");
  Serial.print(now - lastCommandMs);
  Serial.print(",\"uptime_ms\":");
  Serial.print(now);
  Serial.println("}");
}

void setup()
{
  digitalWrite(ENABLE_PIN, HIGH);
  pinMode(ENABLE_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, LOW);
  Serial.begin(115200);
  lastCommandMs = millis();
  lastMotionUpdateUs = micros();
  inputLine.reserve(160);
}

void loop()
{
  readCommands();
  updateMotor();
  publishStatus();
}