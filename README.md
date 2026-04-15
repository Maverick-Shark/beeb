# beeb

BBC Micro Model B/Master core

This is a port of Mike Sterlings great BBC Micro on FPGA with major changes by Stephen Leary, David Banks and others.

## FDC Control for Model B (added by @maverick-shark)

From http://www.adsb.co.uk/bbc/disk_controllers/ddfs_v2.jpg

```
BBC B drive control:
  Bit   Meaning
  ---------------------------
  7,6   Not used
    5   Reset controller
    4   Not used
    3   Double Density select
    2   Side select
    1   Drive select 1
    0   Drive select 0
```

**FDC Control Register**

* Model B (from ddfs_v2 schematic via 74LS174 latch):
   - b[1:0] = drive select (active low), b[2] = side select (inverted)
   - b[3] = density, b[5] = reset
* Master: bit2=reset, bit4=side(active low), bit5=density
   - b[1:0] = drive select (active low), b[2] = reset
   - b[4] = side select (inverted), b[5] = density
