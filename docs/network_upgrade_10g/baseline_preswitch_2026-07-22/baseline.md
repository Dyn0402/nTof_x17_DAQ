# Pre-switch-swap baseline  2026-07-22T16:22:22+02:00

## MAC <-> IP map (192.168.10.0/24) -- use this if switch ports get shuffled
```
192.168.10.43 lladdr 00:0a:35:02:36:2b DELAY 
192.168.10.44 lladdr 00:0a:35:02:36:2c DELAY 
192.168.10.81 lladdr 00:0a:35:02:36:51 DELAY 
192.168.10.82 lladdr 00:0a:35:02:36:52 DELAY 
192.168.10.83 lladdr 00:0a:35:02:36:53 DELAY 
91.189.91.96 FAILED 
91.189.91.97 FAILED 
91.189.91.98 FAILED 
185.125.190.99 FAILED 
185.125.190.100 FAILED 
185.125.190.101 FAILED 
192.168.10.110 lladdr 00:0a:35:02:36:6e DELAY 
192.168.10.111 lladdr 00:0a:35:02:36:6f DELAY 
192.168.10.118 lladdr 00:0a:35:02:36:76 DELAY 
192.168.10.240 lladdr 00:12:5e:00:1b:7e DELAY 
192.168.10.241 lladdr 00:12:5e:00:17:1b DELAY 
192.168.10.242 lladdr 00:12:5e:00:1b:80 DELAY 
192.168.10.243 lladdr 00:12:53:00:1b:81 DELAY 
192.168.10.244 lladdr 00:12:5e:00:1a:66 DELAY 
192.168.10.245 lladdr 00:12:5e:00:17:8b DELAY 
```

## link
```
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp4s0           UP             192.168.10.8/24 fe80::bafb:b3ff:fe57:7a4f/64 
eno1             UP             128.141.177.103/24 fe80::b696:91ff:fe4d:1a95/64 

	Advertised link modes:  10baseT/Full
	Link partner advertised link modes:  10baseT/Half 10baseT/Full
	Link partner advertised pause frame use: No
	Link partner advertised auto-negotiation: No
	Link partner advertised FEC modes: Not reported
	Speed: 1000Mb/s
	Duplex: Full
	Link detected: yes
```

## enp4s0 error counters (all should stay 0)
```
     InErrors: 0
     InDroppedDma: 0
     Queue[0] InErrors: 0
     Queue[0] XdpDrop: 0
     Queue[1] InErrors: 0
     Queue[1] XdpDrop: 0
     Queue[2] InErrors: 0
     Queue[2] XdpDrop: 0
     Queue[3] InErrors: 0
     Queue[3] XdpDrop: 0
```

## ring / mtu
```
Ring parameters for enp4s0:
Pre-set maximums:
RX:			8184
RX Mini:		n/a
RX Jumbo:		n/a
TX:			8184
TX push buff len:	n/a
Current hardware settings:
RX:			2048
RX Mini:		n/a
RX Jumbo:		n/a
TX:			4096
RX Buf Len:		n/a
CQE Size:		n/a
TX Push:		off
RX Push:		off
TX push buff len:	n/a
TCP data split:		n/a
```
