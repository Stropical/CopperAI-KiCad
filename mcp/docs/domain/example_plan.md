Example Mini Plan Output
- Components: U1 MCU STM32G0QFN32 VDD 3.3 V footprint QFN32 5x5 EP; Y1 8 MHz crystal load 8 pF package 3225; J1 USB-C receptacle USB2-only footprint 16-pin; ESD array low-cap USB2 4-channel SOT-23-6.
- Datasheet/links: U1 DS <link>; Y1 DS <link>; J1 DS <link>; TVS DS <link>; LDO DS <link>.
- Pin wiring: USB J1 D+ → U1 PA12; J1 D- → U1 PA11; both through TVS; 90 Ω diff; shield to chassis via 0 Ω. Crystal Y1 XO → U1 PH0 via 22 Ω; Y1 XI → U1 PH1; C1/C2 12 pF to GND. Power J1 VBUS → fuse → 5 V; 5 V → LDO → 3.3 V; decouple U1.
- Layout notes: place crystal/caps next to MCU pins; keep USB pair short, matched, via-paired; fuse and bulk cap at connector; decouplers within 5 mm of each VDD; keep noisy DC-DC away from analog pins.
