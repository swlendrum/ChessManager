#include <Wire.h>
#include <Arduino.h>

// ------------------------------------------------------------
// --- Configuration
// ------------------------------------------------------------
#define UID_LEN 7

// Each Nano uses a different I2C address assigned by the Pi.
#define I2C_ADDR 0x10   // Change to 0x11 for the right-side Nano

// I2C commands
#define CMD_GET_BLOCK 0x01
#define CMD_PING      0x02
#define ERROR_BYTE    0xEE

// Mux I2C addresses (same for both Nanos)
const uint8_t multiplexer_addrs[4] = {
    0x70,  // MUX0_ADDR
    0x71,  // MUX1_ADDR
    0x72,  // MUX2_ADDR
    0x73   // MUX3_ADDR
};

// Map an 8-channel mux to its 2×4 block
struct RC { uint8_t r; uint8_t c; };
const RC CHANNEL_TO_LOCAL_RC[8] = {
    {0,0}, {0,1}, {0,2}, {0,3},
    {1,0}, {1,1}, {1,2}, {1,3}
};

// Halfboard cache: 8 rows × 4 columns
uint8_t halfboard[8][4][UID_LEN];

// EMPTY UID (7 zeros)
const uint8_t EMPTY_UID[UID_LEN] = {0,0,0,0,0,0,0};

// Buffer used when responding to GET_BLOCK
uint8_t tx_buffer[32 * UID_LEN];

// ------------------------------------------------------------
// --- NFC + Multiplexer Helpers (STUBS YOU FILL IN)
// ------------------------------------------------------------

// Select a channel on a TCA9548A-style multiplexer
void activate_mux_channel(uint8_t mux_addr, uint8_t channel) {
    Wire.beginTransmission(mux_addr);
    Wire.write(1 << channel);
    Wire.endTransmission();
    delayMicroseconds(300); // small settle time
}

// MUST be implemented for your NFC readers
bool read_nfc_reader(uint8_t* uid_out) {
    // TODO: Add your specific NFC reading implementation here.
    // Should fill uid_out with UID_LEN bytes and return true on success.
    // For now: return "empty"
    memcpy(uid_out, EMPTY_UID, UID_LEN);
    return false;
}

bool uid_is_valid(const uint8_t* uid) {
    for (int i=0; i<UID_LEN; i++) {
        if (uid[i] != 0) return true;
    }
    return false;
}

// ------------------------------------------------------------
// --- I2C Handlers
// ------------------------------------------------------------

volatile uint8_t last_command = 0;

void on_i2c_receive(int count) {
    if (count > 0) {
        last_command = Wire.read();
    }
}

void on_i2c_request() {
    if (last_command == CMD_GET_BLOCK) {

        // Flatten halfboard → tx_buffer
        uint16_t idx = 0;
        for (uint8_t r = 0; r < 8; r++) {
            for (uint8_t c = 0; c < 4; c++) {
                memcpy(&tx_buffer[idx], halfboard[r][c], UID_LEN);
                idx += UID_LEN;
            }
        }

        Wire.write(tx_buffer, 32 * UID_LEN);

    } else if (last_command == CMD_PING) {
        Wire.write((uint8_t)0x01);
    } else {
        Wire.write((uint8_t)ERROR_BYTE);
    }
}

// ------------------------------------------------------------
// --- Setup
// ------------------------------------------------------------

void setup() {
    Wire.begin(I2C_ADDR);
    Wire.onReceive(on_i2c_receive);
    Wire.onRequest(on_i2c_request);

    // Fill board with EMPTY_UID
    for (int r=0; r<8; r++)
        for (int c=0; c<4; c++)
            memcpy(halfboard[r][c], EMPTY_UID, UID_LEN);
}

// ------------------------------------------------------------
// --- Continuous Scanning Loop
// ------------------------------------------------------------

void loop() {
    uint8_t uid[UID_LEN];

    for (uint8_t m = 0; m < 4; m++) {
        uint8_t mux_addr = multiplexer_addrs[m];
        uint8_t base_row = m * 2;

        for (uint8_t channel = 0; channel < 8; channel++) {

            RC rc = CHANNEL_TO_LOCAL_RC[channel];
            uint8_t global_r = base_row + rc.r;
            uint8_t global_c = rc.c;

            activate_mux_channel(mux_addr, channel);

            bool ok = read_nfc_reader(uid);

            if (ok && uid_is_valid(uid)) {
                memcpy(halfboard[global_r][global_c], uid, UID_LEN);
            } else {
                memcpy(halfboard[global_r][global_c], EMPTY_UID, UID_LEN);
            }
        }
    }

    delay(3);  // small stability delay, tune as needed
}
