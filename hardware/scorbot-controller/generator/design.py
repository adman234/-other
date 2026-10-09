"""Single source of truth for the Scorbot ER-4u master board.

Every part, its symbol, footprint, pin-to-net mapping and rough placement
lives here. gen_library.py, gen_schematic.py and gen_pcb.py all read this
file, so a change here (for example the Pololu carrier pin order) is made
once and the schematic and PCB are regenerated from it.
"""

# ---------------------------------------------------------------------------
# Footprint shorthands
# ---------------------------------------------------------------------------
R0805 = "Resistor_SMD:R_0805_2012Metric"
R1206 = "Resistor_SMD:R_1206_3216Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
LED0805 = "LED_SMD:LED_0805_2012Metric"

# ---------------------------------------------------------------------------
# ER-4u DB50 pinout (SCORBOT-ER 4u User Manual, Chapter 8 "Robot Wiring";
# identical pin numbers to the ER III manual Table D-1).
# Axis order: 1 base, 2 shoulder, 3 elbow, 4 wrist motor A, 5 wrist motor B,
# 6 gripper. The manual's +/- motor labels are irrelevant: firmware sets
# each axis's direction.
# ---------------------------------------------------------------------------
AXES = {
    1: dict(name="BASE",     mot_a=17, mot_b=50, p0=2,  p1=5,  vled=11, gnd=33, sw=23),
    2: dict(name="SHOULDER", mot_a=16, mot_b=49, p0=1,  p1=21, vled=27, gnd=32, sw=7),
    3: dict(name="ELBOW",    mot_a=15, mot_b=48, p0=36, p1=4,  vled=10, gnd=31, sw=24),
    4: dict(name="WRIST_A",  mot_a=14, mot_b=47, p0=35, p1=20, vled=26, gnd=30, sw=8),
    5: dict(name="WRIST_B",  mot_a=13, mot_b=46, p0=18, p1=3,  vled=9,  gnd=29, sw=6),
    6: dict(name="GRIPPER",  mot_a=12, mot_b=45, p0=34, p1=19, vled=25, gnd=28, sw=None),
}
DB50_GRIPPER_SW_NC = 22          # wired in the cable, nothing in the arm
DB50_UNUSED = list(range(37, 45))  # 37-44 unused on ER III and ER-4u


def db50_pin_names():
    names = {}
    for i, a in AXES.items():
        names[a["mot_a"]] = f"M{i}_A"
        names[a["mot_b"]] = f"M{i}_B"
        names[a["p0"]] = f"ENC{i}_P0"
        names[a["p1"]] = f"ENC{i}_P1"
        names[a["vled"]] = f"ENC{i}_VLED"
        names[a["gnd"]] = f"AX{i}_GND"
        if a["sw"]:
            names[a["sw"]] = f"SW{i}"
    names[DB50_GRIPPER_SW_NC] = "SW6_NC"
    for p in DB50_UNUSED:
        names[p] = "NC"
    assert sorted(names) == list(range(1, 51)), "DB50 map must cover pins 1-50"
    return names


# ---------------------------------------------------------------------------
# ESP32-DevKitC V4 (38-pin, WROOM-32E). Pins 1-19 = header J2 (3V3 end
# first), pins 20-38 = header J3 (GND end first). The 3V3/GND end is the
# antenna end; the 5V/CLK end is the USB end.
# ---------------------------------------------------------------------------
DEVKIT_PINS = [
    # J2
    "3V3", "EN", "IO36", "IO39", "IO34", "IO35", "IO32", "IO33", "IO25",
    "IO26", "IO27", "IO14", "IO12", "GND", "IO13", "IO9", "IO10", "IO11", "5V",
    # J3
    "GND", "IO23", "IO22", "IO1", "IO3", "IO21", "GND", "IO19", "IO18", "IO5",
    "IO17", "IO16", "IO4", "IO0", "IO2", "IO15", "IO8", "IO7", "IO6",
]
DEVKIT_ROW_SPACING = 25.4  # mm between J2 and J3 -- MEASURE YOUR BOARD

# ---------------------------------------------------------------------------
# Pololu DRV8874 carrier (#4035).
# !!! PLACEHOLDER PIN ORDER !!! The pin NAMES are right (from Pololu's
# product description); the physical ORDER and spacing below are a guess
# because pololu.com was unreachable when this was generated. Fix
# CARRIER_ROWS / CARRIER_ROW_SPACING from the carrier's pinout drawing and
# re-run the generators.
# ---------------------------------------------------------------------------
CARRIER_ROWS = [
    ["VIN", "VM", "GND", "OUT1", "OUT2", "GND"],                         # power side
    ["PMODE", "IMODE", "EN", "PH", "SLEEP", "FAULT", "CS", "VREF"],     # logic side
]
CARRIER_ROW_SPACING = 12.7  # mm -- PLACEHOLDER


def carrier_pins():
    """Pin number -> name for the carrier, numbered row by row."""
    out, n = {}, 1
    for row in CARRIER_ROWS:
        for name in row:
            out[n] = name
            n += 1
    return out


# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------
PARTS = []


def part(ref, lib, value, fp, nets, group, pcb=None, dnp=False, **extra):
    """nets: {pin_number(str): net_name or None for no-connect}."""
    p = dict(ref=ref, lib=lib, value=value, fp=fp,
             nets={str(k): v for k, v in nets.items()},
             group=group, pcb=pcb, dnp=dnp)
    p.update(extra)
    PARTS.append(p)
    return p


def R(ref, value, a, b, group, fp=R0805, pcb=None, dnp=False):
    return part(ref, "Device:R", value, fp, {1: a, 2: b}, group, pcb, dnp)


def C(ref, value, a, b, group, fp=C0805, pcb=None):
    return part(ref, "Device:C", value, fp, {1: a, 2: b}, group, pcb)


def CP(ref, value, plus, minus, group, fp, pcb=None):
    return part(ref, "Device:C_Polarized", value, fp, {1: plus, 2: minus}, group, pcb)


def LED(ref, value, anode, cathode, group, pcb=None):
    return part(ref, "Device:LED", value, LED0805, {1: cathode, 2: anode}, group, pcb)


# ---- Power input ---------------------------------------------------------
part("J1", "Connector:Barrel_Jack", "12V in (5.5x2.1)",
     "Connector_BarrelJack:BarrelJack_Horizontal",
     {1: "VIN_RAW", 2: "GND"}, "power", pcb=(9, 14, 0))
part("J2", "Connector:Screw_Terminal_01x02", "12V in (alt)",
     "Connector_Phoenix_MSTB:PhoenixContact_MSTBA_2,5_2-G-5,08_1x02_P5.08mm_Horizontal",
     {1: "VIN_RAW", 2: "GND"}, "power", pcb=(5, 32, 90))
part("F1", "Device:Fuse", "5A blade (mini)", "Fuse:Fuseholder_Blade_Mini_Keystone_3568",
     {1: "VIN_RAW", 2: "VIN_F"}, "power", pcb=(28, 22, 90))
part("Q1", "Device:Q_PMOS_GDS", "AOD4185 (rev. polarity)",
     "Package_TO_SOT_SMD:TO-252-2",
     {1: "Q1_G", 2: "VIN_F", 3: "VIN"}, "power", pcb=(30, 44, 0))
R("R1", "10k", "Q1_G", "GND", "power", pcb=(22, 50, 0))
part("D2", "Device:D_Zener", "BZT52C12 (Vgs clamp)", "Diode_SMD:D_SOD-123",
     {1: "VIN", 2: "Q1_G"}, "power", pcb=(22, 54, 0))
part("D1", "Device:D_TVS", "SMBJ18A", "Diode_SMD:D_SMB",
     {1: "VIN", 2: "GND"}, "power", pcb=(40, 44, 90))
CP("C1", "1000uF 25V low-ESR", "VIN", "GND", "power",
   "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm", pcb=(14, 58, 0))
C("C2", "100nF 50V", "VIN", "GND", "power", pcb=(26, 58, 0))

# ---- E-stop (no relay): NC contacts in series with the motor rail --------
part("J3", "Connector:Screw_Terminal_01x02", "E-STOP (NC, in VM line)",
     "Connector_Phoenix_MSTB:PhoenixContact_MSTBA_2,5_2-G-5,08_1x02_P5.08mm_Horizontal",
     {1: "VIN", 2: "VM"}, "power", pcb=(6, 74, 90))
R("R2", "10k (VM bleeder)", "VM", "GND", "power", fp=R1206, pcb=(22, 70, 0))
R("R3", "22k", "VM", "VM_SENSE", "power", pcb=(22, 74, 0))
R("R4", "10k", "VM_SENSE", "GND", "power", pcb=(22, 77, 0))
R("R5", "47k", "VM", "VM_ADC", "power", pcb=(30, 74, 0))
R("R6", "10k", "VM_ADC", "GND", "power", pcb=(30, 77, 0))
C("C3", "100nF", "VM_ADC", "GND", "power", pcb=(30, 80, 0))
R("R7", "4.7k", "VM", "LED_VM", "power", pcb=(38, 70, 0))
LED("D3", "VM on (green)", "LED_VM", "GND", "power", pcb=(38, 74, 0))

# ---- 5 V buck -------------------------------------------------------------
part("U1", "Regulator_Switching:AP63205WU", "AP63205WU", "Package_TO_SOT_SMD:TSOT-23-6",
     {1: "+5V", 2: "BUCK_EN", 3: "VIN", 4: "GND", 5: "BUCK_SW", 6: "BUCK_BST"},
     "buck", pcb=(36, 58, 0))
R("R8", "100k", "VIN", "BUCK_EN", "buck", pcb=(36, 53, 0))
C("C4", "10uF 50V", "VIN", "GND", "buck", fp=C1206, pcb=(42, 53, 90))
C("C5", "10uF 50V", "VIN", "GND", "buck", fp=C1206, pcb=(45, 53, 90))
C("C6", "100nF", "BUCK_BST", "BUCK_SW", "buck", pcb=(36, 62, 0))
part("L1", "Device:L", "6.8uH 3A", "Inductor_SMD:L_Bourns_SRN6045TA",
     {1: "BUCK_SW", 2: "+5V"}, "buck", pcb=(46, 62, 0))
C("C7", "22uF 10V", "+5V", "GND", "buck", fp=C1206, pcb=(42, 68, 90))
C("C8", "22uF 10V", "+5V", "GND", "buck", fp=C1206, pcb=(45, 68, 90))
R("R9", "1k", "+5V", "LED_5V", "buck", pcb=(38, 80, 0))
LED("D4", "5V (green)", "LED_5V", "GND", "buck", pcb=(38, 83, 0))
part("D5", "Device:D_Schottky", "SS14", "Diode_SMD:D_SMA",
     {1: "+5V_DK", 2: "+5V"}, "buck", pcb=(46, 76, 0))

# ---- ESP32 DevKit ---------------------------------------------------------
esp_net = {
    "3V3": "+3V3", "5V": "+5V_DK", "GND": "GND",
    "IO36": "ENC1_A", "IO39": "ENC1_B", "IO34": "ENC2_A", "IO35": "ENC2_B",
    "IO32": "ENC3_A", "IO33": "ENC3_B", "IO25": "ENC4_A", "IO26": "ENC4_B",
    "IO27": "ENC5_A", "IO13": "ENC5_B", "IO4": "ENC6_A", "IO23": "ENC6_B",
    "IO16": "PWM1", "IO17": "PWM2", "IO18": "PWM3", "IO19": "PWM4",
    "IO14": "PWM5", "IO15": "PWM6",
    "IO12": "NSLEEP", "IO21": "SDA", "IO22": "SCL", "IO5": "EXP_INT",
    "IO2": "LED_STATUS",
}
part("U2", "Scorbot:ESP32_DevKitC_V4", "ESP32-DevKitC-32E (38-pin)",
     "Scorbot:ESP32_DevKitC_V4_Socket",
     {i + 1: esp_net.get(n) for i, n in enumerate(DEVKIT_PINS)},
     "mcu", pcb=(14, 98, 270))
R("R10", "10k", "NSLEEP", "GND", "mcu", pcb=(62, 100, 0))
R("R11", "1k", "LED_STATUS", "LED_ST_A", "mcu", pcb=(62, 104, 0))
LED("D6", "STATUS (blue)", "LED_ST_A", "GND", "mcu", pcb=(62, 108, 0))
R("R12", "1k", "+3V3", "LED_3V3", "mcu", pcb=(62, 112, 0))
LED("D7", "3V3 (green)", "LED_3V3", "GND", "mcu", pcb=(62, 116, 0))

# ---- I2C: TCA9555 + ADS7828 -----------------------------------------------
R("R13", "4.7k", "+3V3", "SDA", "i2c", pcb=(72, 70, 0))
R("R14", "4.7k", "+3V3", "SCL", "i2c", pcb=(72, 73, 0))
R("R15", "10k", "+3V3", "EXP_INT", "i2c", pcb=(72, 76, 0))
R("R16", "10k", "+3V3", "NFAULT", "i2c", pcb=(72, 79, 0))
tca = {1: "EXP_INT", 2: "GND", 3: "GND", 21: "GND", 22: "SCL", 23: "SDA",
       24: "+3V3", 12: "GND",
       4: "PH1", 5: "PH2", 6: "PH3", 7: "PH4", 8: "PH5", 9: "PH6",
       10: "EXP_P06", 11: "EXP_P07",
       13: "SW1", 14: "SW2", 15: "SW3", 16: "SW4", 17: "SW5",
       18: "NFAULT", 19: "VM_SENSE", 20: "EXP_P17"}
part("U5", "Interface_Expansion:TCA9555PWR", "TCA9555PWR (0x20)",
     "Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm", tca, "i2c", pcb=(84, 56, 0))
C("C9", "100nF", "+3V3", "GND", "i2c", pcb=(84, 48, 0))
ads = {1: "CS1", 2: "CS2", 3: "CS3", 4: "CS4", 5: "CS5", 6: "CS6",
       7: "VM_ADC", 8: "GND", 9: "GND", 10: "ADC_REF", 11: "GND",
       12: "GND", 13: "GND", 14: "SCL", 15: "SDA", 16: "+3V3"}
part("U6", "Analog_ADC:ADS7828", "ADS7828 (0x48)",
     "Package_SO:TSSOP-16_4.4x5mm_P0.65mm", ads, "i2c", pcb=(104, 56, 0))
C("C10", "100nF", "+3V3", "GND", "i2c", pcb=(104, 49, 0))
C("C11", "2.2uF", "ADC_REF", "GND", "i2c", pcb=(110, 56, 90))
part("J5", "Connector_Generic:Conn_01x04", "I2C expansion",
     "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
     {1: "+3V3", 2: "GND", 3: "SDA", 4: "SCL"}, "i2c", pcb=(84, 112, 90))
part("J6", "Connector_Generic:Conn_01x04", "Spare TCA9555 I/O",
     "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
     {1: "EXP_P06", 2: "EXP_P07", 3: "EXP_P17", 4: "GND"}, "i2c", pcb=(100, 112, 90))

# ---- Motor drivers (Pololu DRV8874 carriers) -------------------------------
cpins = carrier_pins()
for i in range(1, 7):
    nets = {}
    for num, name in cpins.items():
        nets[num] = {
            "VIN": "VM", "VM": None, "GND": "GND", "OUT1": f"MOT{i}_A",
            "OUT2": f"MOT{i}_B", "PMODE": "GND", "IMODE": None,
            "EN": f"PWM{i}", "PH": f"PH{i}", "SLEEP": "NSLEEP",
            "FAULT": "NFAULT", "CS": f"CS{i}", "VREF": f"VREF{i}",
        }[name]
    x = 62 + (i - 1) * 21
    part(f"U{9 + i}", "Scorbot:Pololu_DRV8874_Carrier",
         f"Pololu #4035 DRV8874 (M{i})", "Scorbot:Pololu_DRV8874_Carrier_Socket",
         nets, "drivers", pcb=(x, 6, 0))
    CP(f"C{20 + i}", "100uF 25V", "VM", "GND", "drivers",
       "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm", pcb=(x + 4, 34, 0))
    R(f"R{20 + i}", "DNP: lowers I-limit", f"VREF{i}", "GND", "drivers",
      pcb=(x + 12, 34, 0), dnp=True)

# ---- Encoder + switch front end ---------------------------------------------
# Buffer channel assignment: (chip, input pin, output pin)
LVC_CH = [("U3", 1, 2), ("U3", 3, 4), ("U3", 5, 6), ("U3", 9, 8), ("U3", 11, 10), ("U3", 13, 12),
          ("U4", 1, 2), ("U4", 3, 4), ("U4", 5, 6), ("U4", 9, 8), ("U4", 11, 10), ("U4", 13, 12)]
lvc_nets = {"U3": {14: "+3V3", 7: "GND"}, "U4": {14: "+3V3", 7: "GND"}}
ch = 0
for i in range(1, 7):
    y0 = 44 + (i - 1) * 11
    R(f"R{30 + i}", "47R 0.25W", "+5V", f"ENC{i}_VLED", "encoders", fp=R1206, pcb=(132, y0, 0))
    for k, sig in ((0, "P0"), (1, "P1")):
        n = (i - 1) * 2 + k
        R(f"R{40 + n}", "10k", "+5V", f"ENC{i}_{sig}", "encoders", pcb=(140, y0 + k * 3, 0))
        C(f"C{40 + n}", "1nF", f"ENC{i}_{sig}", "GND", "encoders", pcb=(146, y0 + k * 3, 0))
        chip, pin_in, pin_out = LVC_CH[ch]
        lvc_nets[chip][pin_in] = f"ENC{i}_{sig}"
        lvc_nets[chip][pin_out] = f"ENC{i}_{'A' if sig == 'P0' else 'B'}"
        ch += 1
part("U3", "74xx:74HC14", "74LVC14 (axes 1-3)", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
     lvc_nets["U3"], "encoders", pcb=(120, 56, 90))
part("U4", "74xx:74HC14", "74LVC14 (axes 4-6)", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
     lvc_nets["U4"], "encoders", pcb=(120, 88, 90))
C("C12", "100nF", "+3V3", "GND", "encoders", pcb=(114, 56, 90))
C("C13", "100nF", "+3V3", "GND", "encoders", pcb=(114, 88, 90))
for i in range(1, 6):
    R(f"R{60 + i}", "10k", "+3V3", f"SW{i}", "switches", pcb=(100, 66 + i * 4, 0))
    C(f"C{60 + i}", "100nF", f"SW{i}", "GND", "switches", pcb=(108, 66 + i * 4, 0))

# ---- DB50 (robot cable) ------------------------------------------------------
db = {}
for i, a in AXES.items():
    db[a["mot_a"]] = f"MOT{i}_A"
    db[a["mot_b"]] = f"MOT{i}_B"
    db[a["p0"]] = f"ENC{i}_P0"
    db[a["p1"]] = f"ENC{i}_P1"
    db[a["vled"]] = f"ENC{i}_VLED"
    db[a["gnd"]] = "GND"
    if a["sw"]:
        db[a["sw"]] = f"SW{i}"
db[DB50_GRIPPER_SW_NC] = None
for p in DB50_UNUSED:
    db[p] = None
part("J4", "Scorbot:DD50_Female", "DD-50 female R/A (robot cable)",
     "Scorbot:DD50_Female_Horizontal_P2.77x2.84mm", db, "db50", pcb=(170, 54, 90))

# ---- Power flags + mounting holes -------------------------------------------
for k, net in enumerate(["GND", "VIN", "VIN_RAW", "VM", "+5V", "+5V_DK"]):
    part(f"#FLG0{k + 1}", "power:PWR_FLAG", "PWR_FLAG", "", {1: net}, "flags")
for k, xy in enumerate([(4, 4), (181, 4), (4, 124), (181, 124)]):
    part(f"H{k + 1}", "Mechanical:MountingHole", "M3", "MountingHole:MountingHole_3.2mm_M3",
         {}, "holes", pcb=(xy[0], xy[1], 0))

BOARD_W, BOARD_H = 185.0, 128.0

if __name__ == "__main__":
    from collections import defaultdict
    nets = defaultdict(list)
    for p in PARTS:
        for pin, n in p["nets"].items():
            if n:
                nets[n].append(f"{p['ref']}.{pin}")
    for n in sorted(nets):
        print(f"{n:<12} {len(nets[n]):>3}  {' '.join(nets[n][:8])}{' ...' if len(nets[n]) > 8 else ''}")
    single = [n for n, v in nets.items() if len(v) < 2]
    print("parts:", len(PARTS), "nets:", len(nets), "single-pin nets:", single)
