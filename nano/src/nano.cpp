#include <Arduino.h>
#include <Wire.h>

// ------------------------
// CONFIG CONSTANTS
// ------------------------

#define UID_LEN 7
const uint8_t EMPTY_UID[UID_LEN] = {0};

#define CMD_GET_BLOCK 0x01
#define CMD_PING      0x02

// 4 multiplexers stacked vertically
const uint8_t multiplexer_addrs[4] = {
    0x70,   // your actual address here
    0x71,
    0x72,
    0x73
};

// Maps channel → (local_row, local_col)
// This is identical for each multiplexer
const uint8_t CHANNEL_TO_LOCAL_RC[8][2] = {
    {0,0}, {0,1}, {0,2}, {0,3},
    {1,0}, {1,1}, {1,2}, {1,3}
};

// ------------------------
// Cached half-board (8 rows × 4 columns)
// Each entry = 7-byte UID
// ------------------------
uint8_t halfboard[8][4][UID_LEN];


// ====================================================================
// NFC Reading (replace stubs with real hardware read)
// ====================================================================

// You must implement this for your NFC reader model
// Returns true if a tag is successfully read
bool read_nfc_uid(uint8_t* uid_out) {
    // TODO: Replace with real NFC reader code (PN532, MFRC522, etc.)
    // For now: simulate "no tag"
    memcpy(uid_out, EMPTY_UID, UID_LEN);
    return false;
}


// ====================================================================
// Multiplexer control
// ====================================================================

// Activate a specific channel on a specific multiplexer IC
void activate_mux_channel(uint8_t mux_addr, uint8_t channel) {
    Wire.beginTransmission(mux_addr);
    Wire.write(1 << channel);   // single active channel
    Wire.endTransmission();
    delayMicroseconds(200);     // settle time
}


// ====================================================================
// Main scanning procedure
// ====================================================================

void scan_all_sensors() {
    // For each multiplexer (0..3):
    for (uint8_t m = 0; m < 4; m++) {
        uint8_t mux_addr = multiplexer_addrs[m];
        uint8_t base_row = m * 2;

        for (uint8_t ch = 0; ch < 8; ch++) {
            uint8_t local_r = CHANNEL_TO_LOCAL_RC[ch][0];
            uint8_t local_c = CHANNEL_TO_LOCAL_RC[ch][1];

            uint8_t global_r = base_row + local_r;
            uint8_t global_c = local_c;

            // Select the NFC reader on this channel
            activate_mux_channel(mux_addr, ch);

            uint8_t uid[UID_LEN];
            bool ok = read_nfc_uid(uid);

            if (ok) {
                memcpy(halfboard[global_r][global_c], uid, UID_LEN);
            } else {
                memcpy(halfboard[global_r][global_c], EMPTY_UID, UID_LEN);
            }
        }
    }
}


// ====================================================================
// Serial command handlers
// ====================================================================

void handle_get_block() {
    // Send 8×4 entries = 32 IDs × 7 bytes = 224 raw bytes
    for (uint8_t r = 0; r < 8; r++) {
        for (uint8_t c = 0; c < 4; c++) {
            Serial.write(halfboard[r][c], UID_LEN);
        }
    }
}

void handle_ping() {
    Serial.write((uint8_t)0x01);
}


// ====================================================================
// Setup
// ====================================================================

void setup() {
    Wire.begin();
    Serial.begin(115200);

    // Initialize empty cache
    for (uint8_t r = 0; r < 8; r++) {
        for (uint8_t c = 0; c < 4; c++) {
            memcpy(halfboard[r][c], EMPTY_UID, UID_LEN);
        }
    }

    delay(200);
    Serial.println("Nano Ready");
}


// ====================================================================
// Loop
// ====================================================================

void loop() {
    // 1. Scan half-board continuously
    scan_all_sensors();

    // 2. Handle incoming serial commands
    while (Serial.available() > 0) {
        uint8_t cmd = Serial.read();

        switch (cmd) {
            case CMD_GET_BLOCK:
                handle_get_block();
                break;

            case CMD_PING:
                handle_ping();
                break;

            default:
                // Optional: ignore or send error byte
                Serial.write((uint8_t)0xFF);
                break;
        }
    }

    // Tuning:
    delay(10);   // reduce to speed up scanning
}
