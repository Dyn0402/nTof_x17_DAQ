# N1081B Python SDK

Python client library to control the Nuclear Instruments N1081B programmable logic unit over a WebSocket interface.

It provides a typed API to:
- configure and read back all available functions:

  - Logic functions: Wire, AND, OR, OR-Veto, Veto, Majority, Coincidence Gate, LUT
  - Counting and timing: Scaler, Counter, Counter/Timer, Chronometer, Rate Meter, Rate Meter Advanced
  - Time measurement: Time Tag, Time of Flight, Time over Threshold
  - Signal generation: Pulse Generator, Digital Generator, Pattern Generator

- Acquisition control (start/stop/reset) and result readout for all function types that support it
- Input/output configuration per section: signal standard and impedance
- Input configuration per channel: input enable, gate, delay and signal inversion
- Output configuration per channel: output enable, monostable and signal inversion
- Logic analyser: configuration and status readout for all input and output channels
- System settings: ethernet, clock and configuration file management

It is also possible to set and get the input and output sections and channels configuration parameters.

It implements the logic analyser configuration and data retrieval to acquire the logic status of all input and output channels.

Ethernet, clock and configuration file settings can also be managed through the SDK.

<!-- > The full user documentation is available [here](https://public-repo.pages.nuclearinstruments.eu/x1081/n1081b_sdk_python/) -->

---

## Features

- Simple WebSocket connection handling (connect/disconnect/login, password change)
- Enumerations for all categorical parameters (sections, function types, signal standards, impedance, etc.)
- High-level configuration helpers for each function type, for input/output sections and channels, for the logic analyzer and for the settings management 

---

## Installation

### From PyPI

```bash
pip install n1081b-sdk
```

### From source (this repository)

```bash
git clone https://gitlab.nuclearinstruments.eu/public-repo/n1081/n1081b_sdk_python.git
cd n1081b_sdk_python

# (optional) create a virtualenv
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate

pip install -e .
```

---

## Quick start

Minimal example showing how to connect to a board, configure a section and start acquisition.

```python
from n1081b_sdk import N1081B

# 1. Create device object and connect
device = N1081B("192.168.0.10")
device.connect()

# 2. Login with default password
if not device.login("password"):
    raise RuntimeError("Login failed")

# 3. Configure input of section A in NIM standard
device.set_input_configuration(N1081B.Section.SEC_A, 
                               N1081B.SignalStandard.STANDARD_NIM,
                               N1081B.SignalStandard.STANDARD_NIM,
                               0, N1081B.SignalImpedance.IMPEDANCE_50)

# 4. Configure section A as a scaler
device.set_section_function(N1081B.Section.SEC_A, N1081B.FunctionType.FN_SCALER)

# 5. Configure scaler enabling all four input channels and the input gate
device.configure_scaler(N1081B.Section.SEC_A, True, True, True, True, True)

# 6. Read results
data = device.get_function_results(N1081B.Section.SEC_A)
print(data)

# 7. Disconnect
device.disconnect()
```

See the `examples/` directory for fully worked demos of each function type.

---

## Documentation

The full user guide is generated with MkDocs and published as GitLab Pages.

* Online docs: [`https://public-repo.pages.nuclearinstruments.eu/x1081/n1081b_sdk_python/`](https://public-repo.pages.nuclearinstruments.eu/x1081/n1081b_sdk_python/)

To build the documentation locally:

```bash
pip install -r requirements-mkdocs.txt
mkdocs serve
```

Then open `http://127.0.0.1:8000/` in your browser.

---

## Versioning

Follow [Semantic Versioning](https://semver.org/) for library releases:

* `MAJOR` – incompatible API changes
* `MINOR` – new functionality in a backwards compatible manner
* `PATCH` – backwards compatible bug fixes

Tag releases in Git and publish the same version on PyPI and GitLab Pages.

---

## Contact / Support

* Issues: [https://support.nuclearinstruments.eu](https://support.nuclearinstruments.eu)
