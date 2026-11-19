// User-callable functions exposed by the cisco-pt-mcp bridge.
// Each returns { success: bool, ... } and is invoked via $se('runCode', 'return <fn>(<args>);').

function fail(prefix, err) {
  var msg = (err && (err.message || String(err))) || "unknown error";
  return { success: false, error: prefix ? prefix + ": " + msg : msg };
}

// Device-type groups for 'auto' cable resolution: interconnect gear
// (switch=1, bridge=3, hub=4, repeater=5, L3-switch=16) vs host-like
// endpoints (routers, PCs, servers...). Same group -> cross first,
// different group -> straight first.
function ptLinkGroup(deviceType) {
  var interconnect = { 1: 1, 3: 1, 4: 1, 5: 1, 16: 1 };
  return interconnect[deviceType] ? "interconnect" : "host";
}

// Ordered candidate link types for 'auto', chosen from the two port
// types (Port.getType(): eConsole=0, eAux=1, eCopperEthernet=2,
// eCopperFastEthernet=3, eCopperGigabitEthernet=4, eFiber*=5-6,
// eSerial=7, eSmartSerial=8, eUsbPort=37, eCustomIO=39, eTty=42,
// eUsbConsole=43). createLink() validates compatibility itself, so the
// first candidate that returns true wins.
function autoLinkCandidates(device1Name, port1Name, device2Name, port2Name) {
  var d1 = ipc.network().getDevice(device1Name);
  var d2 = ipc.network().getDevice(device2Name);
  var t1 = -1, t2 = -1;
  if (d1 && d2) {
    var p1 = d1.getPort(port1Name);
    var p2 = d2.getPort(port2Name);
    if (p1) t1 = p1.getType();
    if (p2) t2 = p2.getType();
  } else {
    return ["straight", "cross"];
  }

  var copper = { 2: 1, 3: 1, 4: 1 };
  if (copper[t1] && copper[t2]) {
    return ptLinkGroup(d1.getType()) === ptLinkGroup(d2.getType())
      ? ["cross", "straight"]
      : ["straight", "cross"];
  }
  if (t1 === 7 || t1 === 8 || t2 === 7 || t2 === 8) return ["serial"];
  if (t1 === 5 || t1 === 6 || t2 === 5 || t2 === 6) return ["fiber"];
  if (t1 === 39 || t2 === 39) return ["custom_io"];
  if (t1 === 37 || t2 === 37) return ["usb"];
  if (t1 === 0 || t1 === 1 || t1 === 42 || t1 === 43 ||
      t2 === 0 || t2 === 1 || t2 === 42 || t2 === 43) {
    return ["console", "roll"];
  }
  return ["straight", "cross", "console", "roll", "serial", "fiber",
          "coaxial", "phone", "cable", "octal", "cellular", "usb", "custom_io"];
}

// "deviceName|portName" key for in_use tracking. Port names repeat across
// devices (every PC has "FastEthernet0"), so resolve the owner via
// Port.getOwnerDevice() when the PT version provides it.
function portOwnerKey(port) {
  try {
    if (port.getOwnerDevice) {
      return port.getOwnerDevice().getName() + "|" + port.getName();
    }
  } catch (e) {}
  return "|" + port.getName();
}

function portOwnerName(port, fallbackMap) {
  try {
    if (port && port.getOwnerDevice) {
      return port.getOwnerDevice().getName();
    }
  } catch (e) {}
  return port ? fallbackMap[port.getName()] : null;
}

addDevice = function (deviceName, deviceModel, x, y) {
  try {
    var deviceType = allDeviceTypes[deviceModel];

    if (deviceType === undefined) {
      return {
        success: false,
        error: "Unknown device model: " + (deviceModel),
      };
    }

    var originalDeviceName = ipc
      .appWindow()
      .getActiveWorkspace()
      .getLogicalWorkspace()
      .addDevice(deviceType, deviceModel, x, y);

    if (!originalDeviceName) {
      return {
        success: false,
        error: "Failed to add device " + (deviceName) + " (" + (deviceModel) + ")",
      };
    }

    var device = ipc.network().getDevice(originalDeviceName);
    device.setName(deviceName);

    if (deviceType <= 1 || deviceType == 16) {
      device.skipBoot();
    }

    return {
      success: true,
      message: "Device " + (deviceName) + " added successfully",
    };
  } catch (error) {
    return fail("Error adding device", error);
  }
};

addModule = function (deviceName, slot, model) {
  try {
    var device = ipc.network().getDevice(deviceName);

    if (!device) {
      return {
        success: false,
        error: "Device " + (deviceName) + " not found",
      };
    }

    var moduleType = allModuleTypes[model];

    if (moduleType === undefined) {
      return {
        success: false,
        error: "Unknown module model: " + (model),
      };
    }

    // The API signature is addModule(string slotPath, ModuleType, model) —
    // the slot is a PATH string, not a bare index. Candidates: the given
    // slot plus every slot path discoverable in the device's module tree.
    var candidates = [];
    if (slot !== undefined && slot !== null && slot !== "") {
      candidates.push(String(slot));
    }
    try {
      var root = device.getRootModule && device.getRootModule();
      var mc = root && root.getModuleCount ? root.getModuleCount() : 0;
      for (var i = 0; i < mc; i++) {
        var child = root.getModuleAt(i);
        var path = child && child.getSlotPath ? child.getSlotPath() : null;
        if (path) candidates.push(path);
      }
    } catch (e) {}

    var powerState = device.getPower();
    device.setPower(false);

    var result = false;
    var usedPath = null;
    for (var j = 0; j < candidates.length; j++) {
      try {
        result = device.addModule(candidates[j], moduleType, model);
      } catch (e2) {
        result = false;
      }
      if (result === true) {
        usedPath = candidates[j];
        break;
      }
    }

    if (powerState) {
      device.setPower(true);
      device.skipBoot();
    }

    if (result != true) {
      return {
        success: false,
        error: "Failed to add module " + (model) + " to " + (deviceName) + " — every slot path tried was rejected (tried: " + candidates.join(", ") + "). The slot is a path string like \"0/0\"; install by hand only if the correct path was not among them (open the device -> Physical tab -> drag " + (model) + " into a free bay).",
      };
    }

    return {
      success: true,
      message: "Module " + (model) + " added to " + (deviceName) + " slot " + usedPath,
    };
  } catch (error) {
    return fail("Error adding module", error);
  }
};

// Ports like "RS 232", "Console" and WIC serials are never listed by
// getPortCount, but createLink() still resolves them by name. When the
// caller's port names fail, retry these known hidden names — wrong
// guesses just make createLink return false, so the sweep is cheap.
var HIDDEN_PORT_NAMES = {
  console: ["RS 232", "RS232", "RS-232", "Console", "AUX", "Aux"],
  roll:    ["RS 232", "RS232", "RS-232", "Console", "AUX", "Aux"],
  serial:  ["Serial0/0/0", "Serial0/0/1", "Serial0/1/0", "Serial0/1/1",
            "Serial0/2/0", "Serial0/2/1", "Serial0/3/0", "Serial0/3/1"],
  usb:     ["USB", "USB0", "USB1"],
  coaxial: ["Coaxial", "Coaxial0"],
};

addLink = function (
  device1Name,
  device1Interface,
  device2Name,
  device2Interface,
  linkType
) {
  try {
    var workspace = ipc
      .appWindow()
      .getActiveWorkspace()
      .getLogicalWorkspace();

    var candidates;
    if (linkType === "auto") {
      candidates = autoLinkCandidates(
        device1Name, device1Interface, device2Name, device2Interface
      );
    } else {
      if (allLinkTypes[linkType] === undefined) {
        return {
          success: false,
          error: "Unknown link type: " + (linkType),
        };
      }
      candidates = [linkType];
    }

    var result = false;
    var usedType = null;
    var usedPort1 = device1Interface;
    var usedPort2 = device2Interface;
    outer:
    for (var i = 0; i < candidates.length; i++) {
      var pool = HIDDEN_PORT_NAMES[candidates[i]] || [];
      var names1 = [device1Interface].concat(pool);
      var names2 = [device2Interface].concat(pool);
      for (var a = 0; a < names1.length; a++) {
        for (var b = 0; b < names2.length; b++) {
          try {
            result = workspace.createLink(
              device1Name, names1[a],
              device2Name, names2[b],
              allLinkTypes[candidates[i]]
            );
          } catch (e) {
            result = false;
          }
          if (result === true) {
            usedType = candidates[i];
            usedPort1 = names1[a];
            usedPort2 = names2[b];
            break outer;
          }
        }
      }
    }

    if (result != true) {
      var hint = ". Check that both ports exist and match the cable type.";
      if (linkType === "console" || linkType === "roll") {
        hint = ". Console cables use the hidden \"RS 232\" (PC) and \"Console\" (router) ports — both were already tried by name. If this still fails, connect the cable by hand in Packet Tracer.";
      } else if (linkType === "serial") {
        hint = ". Serial links need a WIC-2T/HWIC-2T/NIM-2T module installed by hand (device Physical tab); module bays are not exposed by the PT API. Serial0/x/y port names were already tried.";
      }
      return {
        success: false,
        error: "Failed to create link between " + (device1Name) + ":" + (device1Interface) + " and " + (device2Name) + ":" + (device2Interface) + hint,
      };
    }

    return {
      success: true,
      message:
        "Link created between " + device1Name + ":" + usedPort1 +
        " and " + device2Name + ":" + usedPort2 +
        (linkType === "auto" ? " (auto -> " + usedType + ")" : ""),
    };
  } catch (error) {
    return fail("Error creating link", error);
  }
};

configurePcIp = function (
  deviceName,
  dhcpEnabled,
  ipaddress,
  subnetMask,
  defaultGateway,
  dnsServer
) {
  try {
    var device = ipc.network().getDevice(deviceName);

    if (!device) {
      return {
        success: false,
        error: "Device " + (deviceName) + " not found",
      };
    }

    var port = device.getPort("FastEthernet0");

    if (!port) {
      return {
        success: false,
        error: "FastEthernet0 port not found on " + (deviceName),
      };
    }

    if (dhcpEnabled !== undefined && dhcpEnabled !== null) {
      device.setDhcpFlag(dhcpEnabled);
    }
    if (ipaddress && subnetMask) port.setIpSubnetMask(ipaddress, subnetMask);
    if (defaultGateway) port.setDefaultGateway(defaultGateway);
    if (dnsServer) port.setDnsServerIp(dnsServer);

    return {
      success: true,
      message: "IP configuration applied to " + (deviceName),
    };
  } catch (error) {
    return fail("Error configuring PC IP", error);
  }
};

configureIosDevice = function (deviceName, commands) {
  try {
    var device = ipc.network().getDevice(deviceName);

    if (!device) {
      return {
        success: false,
        error: "Device " + (deviceName) + " not found",
      };
    }

    device.skipBoot();
    var commandsArray = commands.split("\n");
    device.enterCommand("!", "global");

    for (var c = 0; c < commandsArray.length; c++) {
      var command = commandsArray[c];
      if (command.trim()) {
        device.enterCommand(command, "");
      }
    }

    device.enterCommand("write memory", "enable");

    return {
      success: true,
      message: "Configuration applied to " + (deviceName) + " (" + (commandsArray.length) + " commands)",
    };
  } catch (error) {
    return fail("Error configuring IOS device", error);
  }
};

// PC Command Prompt support. Commands like `ping` run asynchronously for
// several seconds while runCode calls are synchronous, so the work is
// split: startPcCommand() snapshots the current output length and issues
// the command, readPcOutput() reports output grown since that baseline.
var pcCommandBaselines = {};

startPcCommand = function (deviceName, command) {
  try {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
      return { success: false, error: "Device " + (deviceName) + " not found" };
    }
    var cli = device.getCommandLine ? device.getCommandLine() : null;
    if (!cli) {
      return {
        success: false,
        error: "Device " + (deviceName) + " has no command line (PC/Laptop/Server only)",
      };
    }
    var base = 0;
    try {
      var out = cli.getOutput();
      if (out) base = String(out).length;
    } catch (e) {}
    pcCommandBaselines[deviceName] = base;
    cli.enterCommand(command);
    return { success: true, baseline: base };
  } catch (error) {
    return fail("Error starting PC command", error);
  }
};

readPcOutput = function (deviceName) {
  try {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
      return { success: false, error: "Device " + (deviceName) + " not found" };
    }
    var cli = device.getCommandLine ? device.getCommandLine() : null;
    if (!cli) {
      return {
        success: false,
        error: "Device " + (deviceName) + " has no command line",
      };
    }
    var full = "";
    try { full = String(cli.getOutput() || ""); } catch (e) {}
    var base = pcCommandBaselines[deviceName] || 0;
    var prompt = "";
    try { prompt = String(cli.getPrompt ? (cli.getPrompt() || "") : ""); } catch (e2) {}
    return {
      success: true,
      output: full.slice(base),
      prompt: prompt,
    };
  } catch (error) {
    return fail("Error reading PC output", error);
  }
};

getNetwork = function () {
  try {
    var deviceCount = ipc.network().getDeviceCount();
    var devices = [];
    var connections = [];

    // Pass 1: collect in_use ports, keyed by owner + port name. Bare port
    // names collide across devices (every PC has "FastEthernet0").
    var inUseSet = {};
    var linkCount = ipc.network().getLinkCount();
    for (var li = 0; li < linkCount; li++) {
      var link = ipc.network().getLinkAt(li);
      var p1 = link.getPort1();
      var p2 = link.getPort2();
      if (p1) inUseSet[portOwnerKey(p1)] = true;
      if (p2) inUseSet[portOwnerKey(p2)] = true;
    }

    // Pass 2: devices + interfaces, and a portName -> deviceName map for
    // Pass 3. Hidden ports (Console, "RS 232", WIC serials...) are never
    // enumerated here — Pass 3 merges them from live connections.
    var portOwner = {};
    var deviceIndex = {};
    var ifNameSets = {};
    for (var i = 0; i < deviceCount; i++) {
      var device = ipc.network().getDeviceAt(i);
      var deviceName = device.getName();

      var interfaces = [];
      var ifSet = {};
      var portCount = device.getPortCount();
      for (var j = 0; j < portCount; j++) {
        var port = device.getPortAt(j);
        if (port) {
          var pname = port.getName();
          portOwner[pname] = deviceName;
          ifSet[pname] = true;
          var busy = inUseSet[deviceName + "|" + pname] === true
            || inUseSet["|" + pname] === true;
          interfaces.push({ name: pname, in_use: busy });
        }
      }

      deviceIndex[deviceName] = devices.length;
      ifNameSets[deviceName] = ifSet;
      devices.push({
        name: deviceName,
        model: device.getModel(),
        type: device.getType(),
        interfaces: interfaces,
      });
    }

    // Pass 3: connections — resolve each endpoint's owner via
    // Port.getOwnerDevice() (name-only lookup collides across devices).
    // Hidden ports surface here, so add them to the device's interface
    // list marked in_use.
    for (var k = 0; k < linkCount; k++) {
      var lnk = ipc.network().getLinkAt(k);
      var port1 = lnk.getPort1();
      var port2 = lnk.getPort2();
      var device1Name = portOwnerName(port1, portOwner);
      var device2Name = portOwnerName(port2, portOwner);
      if (device1Name && device2Name) {
        var n1 = port1.getName();
        var n2 = port2.getName();
        connections.push({
          from: device1Name,
          fromInterface: n1,
          to: device2Name,
          toInterface: n2,
          type: lnk.getConnectionType(),
        });
        if (ifNameSets[device1Name] && !ifNameSets[device1Name][n1]) {
          devices[deviceIndex[device1Name]].interfaces.push({
            name: n1, in_use: true,
          });
          ifNameSets[device1Name][n1] = true;
        }
        if (ifNameSets[device2Name] && !ifNameSets[device2Name][n2]) {
          devices[deviceIndex[device2Name]].interfaces.push({
            name: n2, in_use: true,
          });
          ifNameSets[device2Name][n2] = true;
        }
      }
    }

    return {
      success: true,
      result: {
        deviceCount: devices.length,
        connectionCount: connections.length,
        devices: devices,
        connections: connections,
      },
    };
  } catch (error) {
    return fail("", error);
  }
};

getDeviceInfo = function (deviceName) {
  try {
    var net = getNetwork();
    if (!net || !net.success) {
      return net || { success: false, error: "getNetwork failed" };
    }
    var devices = net.result.devices;
    var connections = net.result.connections;
    for (var i = 0; i < devices.length; i++) {
      if (devices[i].name === deviceName) {
        var related = [];
        for (var j = 0; j < connections.length; j++) {
          var c = connections[j];
          if (c.from === deviceName || c.to === deviceName) related.push(c);
        }
        return {
          success: true,
          result: {
            device: devices[i],
            connections: related,
          },
        };
      }
    }
    return {
      success: false,
      error: "Device " + (deviceName) + " not found",
    };
  } catch (error) {
    return fail("Error getting device info", error);
  }
};

removeDevice = function (deviceNames) {
  try {
    var devicesToRemove = [];
    if (typeof deviceNames === "string") {
      devicesToRemove = [deviceNames];
    } else if (Array.isArray(deviceNames)) {
      devicesToRemove = deviceNames;
    } else {
      return {
        success: false,
        error:
          "Invalid input: provide a device name string or array of device names",
      };
    }

    var workspace = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
    var results = [];
    var successCount = 0;
    var failCount = 0;

    for (var i = 0; i < devicesToRemove.length; i++) {
      var deviceName = devicesToRemove[i];
      var device = ipc.network().getDevice(deviceName);

      if (!device) {
        results.push({
          device: deviceName,
          success: false,
          error: "Device not found",
        });
        failCount++;
      } else {
        var result = workspace.removeDevice(deviceName);

        if (result === true) {
          results.push({
            device: deviceName,
            success: true,
            message: "Removed successfully",
          });
          successCount++;
        } else {
          results.push({
            device: deviceName,
            success: false,
            error: "Failed to remove",
          });
          failCount++;
        }
      }
    }

    return {
      success: failCount === 0,
      totalDevices: devicesToRemove.length,
      successCount: successCount,
      failCount: failCount,
      results: results,
    };
  } catch (error) {
    return fail("Error removing devices", error);
  }
};

setSimulationMode = function (toSimMode) {
  try {
    var sim = ipc.simulation();
    var current = sim.isSimulationMode();
    if (current === toSimMode) {
      return {
        success: true,
        message: "Already in " + (toSimMode ? "simulation" : "realtime") + " mode",
        mode: toSimMode ? "simulation" : "realtime",
      };
    }
    sim.setSimulationMode(toSimMode);
    return {
      success: true,
      message: "Switched to " + (toSimMode ? "simulation" : "realtime") + " mode",
      mode: toSimMode ? "simulation" : "realtime",
    };
  } catch (error) {
    return fail("Error setting simulation mode", error);
  }
};

getSimulationStatus = function () {
  try {
    var sim = ipc.simulation();
    var isSimMode = sim.isSimulationMode();
    var result = { mode: isSimMode ? "simulation" : "realtime" };
    if (isSimMode) {
      result.currentTime = sim.getCurrentSimTime();
      result.frameCount = sim.getFrameInstanceCount();
      result.currentFrameIndex = sim.getCurrentFrameInstanceIndex();
    }
    return { success: true, result: result };
  } catch (error) {
    return fail("Error getting simulation status", error);
  }
};

stepSimulation = function (direction, steps) {
  try {
    var sim = ipc.simulation();
    if (!sim.isSimulationMode()) {
      return {
        success: false,
        error: "Not in simulation mode. Call setSimulationMode(true) first.",
      };
    }
    if (direction === "reset") {
      sim.resetSimulation();
      return { success: true, message: "Simulation reset" };
    }
    var n = steps && steps >= 1 ? Math.min(steps, 100) : 1;
    for (var i = 0; i < n; i++) {
      if (direction === "forward") {
        sim.forward();
      } else if (direction === "backward") {
        sim.backward();
      } else {
        return { success: false, error: "Unknown direction: " + direction };
      }
    }
    return {
      success: true,
      message: direction + " " + n + " step(s)",
      currentTime: sim.getCurrentSimTime(),
      frameCount: sim.getFrameInstanceCount(),
    };
  } catch (error) {
    return fail("Error stepping simulation", error);
  }
};

var PDU_TRAFFIC_TYPES = {
  ICMP: 0,
  TCP: 1,
  UDP: 2,
  HTTP: 17,
  HTTPS: 18,
  DNS: 19,
};

sendPdu = function (sourceDevice, destinationDevice) {
  try {
    var sim = ipc.simulation();
    var modeEnabled = false;
    if (!sim.isSimulationMode()) {
      sim.setSimulationMode(true);
      modeEnabled = true;
    }
    if (!ipc.network().getDevice(sourceDevice)) {
      return { success: false, error: "Source device not found: " + sourceDevice };
    }
    if (!ipc.network().getDevice(destinationDevice)) {
      return { success: false, error: "Destination device not found: " + destinationDevice };
    }
    var errCode = ipc.appWindow().getUserCreatedPDU().addSimplePdu(sourceDevice, destinationDevice);
    // ADD_PDU_ERROR: 0 / falsy = success
    var errStr = String(errCode);
    if (errCode && errStr !== "0") {
      return { success: false, error: "PT rejected PDU (ADD_PDU_ERROR=" + errStr + ")" };
    }
    return {
      success: true,
      message: "ICMP PDU added from " + sourceDevice + " to " + destinationDevice,
      simulationModeEnabled: modeEnabled,
    };
  } catch (error) {
    return fail("Error sending PDU", error);
  }
};

renameDevice = function (deviceName, newName) {
  try {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
      return { success: false, error: "Device not found: " + deviceName };
    }
    device.setName(newName);
    return { success: true, message: "Renamed " + deviceName + " to " + newName };
  } catch (error) {
    return fail("Error renaming device", error);
  }
};

moveDevice = function (deviceName, x, y) {
  try {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
      return { success: false, error: "Device not found: " + deviceName };
    }
    device.moveToLocation(x, y);
    return {
      success: true,
      message: "Moved " + deviceName + " to (" + x + ", " + y + ")",
    };
  } catch (error) {
    return fail("Error moving device", error);
  }
};

// Maps both numeric and C++ enum-string forms of eTrafficType to readable names.
// PT's JS host may expose the enum as "0" or as "eTrafficType_Icmp" — handle both.
var TRAFFIC_TYPE_NAMES = {
  "0": "ICMP",  "eTrafficType_Icmp": "ICMP",
  "1": "TCP",   "eTrafficType_Tcp": "TCP",
  "2": "UDP",   "eTrafficType_Udp": "UDP",
  "3": "RIPv1", "eTrafficType_RipV1": "RIPv1",
  "4": "RIPv2", "eTrafficType_RipV2": "RIPv2",
  "5": "ARP",   "eTrafficType_Arp": "ARP",
  "6": "CDP",   "eTrafficType_Cdp": "CDP",
  "7": "DHCP",  "eTrafficType_Dhcp": "DHCP",
  "11": "STP",  "eTrafficType_Stp": "STP",
  "12": "OSPF", "eTrafficType_Ospf": "OSPF",
  "13": "DTP",  "eTrafficType_Dtp": "DTP",
  "17": "HTTP", "eTrafficType_Http": "HTTP",
  "18": "HTTPS","eTrafficType_Https": "HTTPS",
  "19": "DNS",  "eTrafficType_Dns": "DNS",
  "36": "BGP",  "eTrafficType_Bgp": "BGP",
  "1000": "Custom", "eTrafficType_Custom": "Custom",
};

getPduResults = function (types) {
  try {
    var sim = ipc.simulation();
    if (!sim.isSimulationMode()) {
      return { success: false, error: "Not in simulation mode. Call setSimulationMode(true) first." };
    }

    var typeFilter = null;
    if (Array.isArray(types) && types.length > 0) {
      typeFilter = {};
      for (var t = 0; t < types.length; t++) typeFilter[types[t].toUpperCase()] = true;
    }

    var total = sim.getFrameInstanceCount();
    var frames = [];
    for (var i = 0; i < total; i++) {
      var fi = sim.getFrameInstanceAt(i);
      if (!fi) continue;

      var rawType = String(fi.getUserTrafficType());
      var typeName = TRAFFIC_TYPE_NAMES[rawType] || rawType;

      if (typeFilter && !typeFilter[typeName.toUpperCase()]) continue;

      var status = "unknown";
      if (fi.isFrameAccepted())          status = "accepted";
      else if (fi.isFrameDropped())      status = "dropped";
      else if (fi.isFrameNotForwarded()) status = "not_forwarded";
      else if (fi.isFrameUnexpected())   status = "unexpected";
      else if (fi.isFrameCollidedOnLink() || fi.isFrameCollidedAtDevice()) status = "collision";
      else if (fi.isFrameBuffered())     status = "buffered";
      else if (fi.isFrameOnTransit())    status = "in_transit";
      else if (fi.isFrameSent())         status = "sent";

      frames.push({
        index: i,
        source: fi.getSourceString(),
        destination: fi.getDestinationString(),
        trafficType: typeName,
        status: status,
      });
    }
    return {
      success: true,
      result: { totalFrames: total, shown: frames.length, frames: frames },
    };
  } catch (error) {
    return fail("Error getting PDU results", error);
  }
};

getCommandLog = function (deviceName, limit) {
  try {
    var log = ipc.commandLog();
    var total = log.getEntryCount();
    var cap = limit && limit > 0 ? Math.min(limit, 500) : 50;
    var entries = [];

    for (var i = total - 1; i >= 0 && entries.length < cap; i--) {
      var entry = log.getEntryAt(i);
      if (!entry) continue;
      var dev = entry.getDeviceName();
      if (deviceName && dev !== deviceName) continue;
      entries.push({
        timestamp: entry.getTimeToString(),
        device: dev,
        prompt: entry.getPrompt(),
        command: entry.getCommand(),
        resolvedCommand: entry.getResolvedCommand(),
      });
    }

    return {
      success: true,
      result: { totalEntries: total, returned: entries.length, entries: entries },
    };
  } catch (error) {
    return fail("Error getting command log", error);
  }
};

setPower = function (deviceName, power) {
  try {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
      return { success: false, error: "Device not found: " + deviceName };
    }
    device.setPower(power);
    return {
      success: true,
      message: deviceName + " powered " + (power ? "on" : "off"),
    };
  } catch (error) {
    return fail("Error setting device power", error);
  }
};

removeLink = function (links) {
  try {
    var linksToRemove = [];

    if (typeof links === "object" && links !== null && !Array.isArray(links)) {
      linksToRemove = [links];
    } else if (Array.isArray(links)) {
      linksToRemove = links;
    } else {
      return {
        success: false,
        error:
          "Invalid input: provide link object {device, port} or array of link objects",
      };
    }

    var workspace = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
    var results = [];
    var successCount = 0;
    var failCount = 0;

    for (var i = 0; i < linksToRemove.length; i++) {
      var link = linksToRemove[i];
      var deviceName = link.device || link.deviceName;
      var portName = link.port || link.portName;

      if (!deviceName || !portName) {
        results.push({
          device: deviceName,
          port: portName,
          success: false,
          error: "Missing device or port",
        });
        failCount++;
        continue;
      }

      var device = ipc.network().getDevice(deviceName);
      if (!device) {
        results.push({
          device: deviceName,
          port: portName,
          success: false,
          error: "Device not found",
        });
        failCount++;
        continue;
      }

      var result = workspace.deleteLink(deviceName, portName);

      if (result === true) {
        results.push({
          device: deviceName,
          port: portName,
          success: true,
          message: "Link removed successfully",
        });
        successCount++;
      } else {
        results.push({
          device: deviceName,
          port: portName,
          success: false,
          error: "Failed to remove link",
        });
        failCount++;
      }
    }

    return {
      success: failCount === 0,
      totalLinks: linksToRemove.length,
      successCount: successCount,
      failCount: failCount,
      results: results,
    };
  } catch (error) {
    return fail("Error removing links", error);
  }
};

// Batch helper: devices -> links -> pcConfigs in one MCP round-trip.
buildTopology = function (devices, links, pcConfigs, stopOnError) {
  try {
    var stop = stopOnError !== false;
    var deviceResults = [];
    var linkResults = [];
    var pcResults = [];
    var failed = false;

    var deviceList = Array.isArray(devices) ? devices : [];
    var linkList = Array.isArray(links) ? links : [];
    var pcList = Array.isArray(pcConfigs) ? pcConfigs : [];

    var i;
    var item;
    var r;

    for (i = 0; i < deviceList.length; i++) {
      item = deviceList[i] || {};
      r = addDevice(item.deviceName, item.deviceModel, item.x, item.y);
      deviceResults.push({
        deviceName: item.deviceName,
        success: !!(r && r.success),
        message: r && (r.message || r.error),
      });
      if (!(r && r.success)) {
        failed = true;
        if (stop) {
          return {
            success: false,
            stoppedEarly: true,
            stage: "devices",
            deviceResults: deviceResults,
            linkResults: linkResults,
            pcResults: pcResults,
            error: (r && r.error) || "addDevice failed",
          };
        }
      }
    }

    for (i = 0; i < linkList.length; i++) {
      item = linkList[i] || {};
      r = addLink(
        item.device1Name,
        item.device1Interface,
        item.device2Name,
        item.device2Interface,
        item.linkType
      );
      linkResults.push({
        device1Name: item.device1Name,
        device2Name: item.device2Name,
        success: !!(r && r.success),
        message: r && (r.message || r.error),
      });
      if (!(r && r.success)) {
        failed = true;
        if (stop) {
          return {
            success: false,
            stoppedEarly: true,
            stage: "links",
            deviceResults: deviceResults,
            linkResults: linkResults,
            pcResults: pcResults,
            error: (r && r.error) || "addLink failed",
          };
        }
      }
    }

    for (i = 0; i < pcList.length; i++) {
      item = pcList[i] || {};
      r = configurePcIp(
        item.deviceName,
        item.dhcpEnabled,
        item.ipaddress,
        item.subnetMask,
        item.defaultGateway,
        item.dnsServer
      );
      pcResults.push({
        deviceName: item.deviceName,
        success: !!(r && r.success),
        message: r && (r.message || r.error),
      });
      if (!(r && r.success)) {
        failed = true;
        if (stop) {
          return {
            success: false,
            stoppedEarly: true,
            stage: "pcConfigs",
            deviceResults: deviceResults,
            linkResults: linkResults,
            pcResults: pcResults,
            error: (r && r.error) || "configurePcIp failed",
          };
        }
      }
    }

    return {
      success: !failed,
      stoppedEarly: false,
      deviceCount: deviceList.length,
      linkCount: linkList.length,
      pcConfigCount: pcList.length,
      deviceResults: deviceResults,
      linkResults: linkResults,
      pcResults: pcResults,
    };
  } catch (error) {
    return fail("Error building topology", error);
  }
};
