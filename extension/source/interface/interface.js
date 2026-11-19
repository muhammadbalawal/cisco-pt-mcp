/* global io, $se */
// Webview-side bridge: forwards Socket.IO tool_call events to PT's scripting
// host via $se('runCode', ...) and emits the result back as tool_result.
// Wire protocol must match mcp_server/bridge.py.

(function () {
  var MCP_URL = "http://127.0.0.1:7531";

  var $statusDot   = document.getElementById("status-dot");
  var $statusText  = document.getElementById("status-text");
  var $sid         = document.getElementById("sid");
  var $toolCount   = document.getElementById("tool-count");
  var $log         = document.getElementById("log");

  var toolsHandled = 0;

  function setStatus(state, label) {
    if ($statusDot)  $statusDot.className = "dot " + state;
    if ($statusText) $statusText.textContent = label || state;
  }

  function logLine(text, cls) {
    if (!$log) return;
    var line = document.createElement("div");
    line.className = "line" + (cls ? " " + cls : "");
    var ts = new Date().toTimeString().slice(0, 8);
    line.innerHTML = '<span class="ts">' + ts + "</span>  " + escapeHtml(text);
    $log.appendChild(line);
    // Cap log at ~200 lines.
    while ($log.childNodes.length > 200) $log.removeChild($log.firstChild);
    $log.scrollTop = $log.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function incrementToolCount() {
    toolsHandled++;
    if ($toolCount) $toolCount.textContent = String(toolsHandled);
  }

  function emitToolResult(socket, tcid, tool, args, result) {
    if (!socket.connected) return;
    socket.emit("tool_result", {
      tool_call_id: tcid,
      tool_name:    tool,
      tool_input:   args,
      result:       result,
    });
  }

  function buildErrorResult(tool, args, message) {
    return {
      success: false,
      error:   message,
      tool:    tool,
      args:    args,
    };
  }

  function serializePTArgument(value) {
    if (typeof value === "string") {
      return '"' + value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
                        .replace(/\n/g, "\\n").replace(/\r/g, "\\r")
                        .replace(/\t/g, "\\t") + '"';
    }
    if (value === null || value === undefined) return "undefined";
    if (typeof value === "boolean") return String(value);
    if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  // $se's JS->host serialization turns JS arrays into plain objects like
  // {0: .., 1: .., length: N} (an empty array arrives as {length: 0}).
  // Rebuild real arrays wherever that exact shape appears.
  function fixArrayShapes(v) {
    if (Array.isArray(v)) {
      for (var i = 0; i < v.length; i++) v[i] = fixArrayShapes(v[i]);
      return v;
    }
    if (v && typeof v === "object") {
      var keys = Object.keys(v);
      var n = v.length;
      if (typeof n === "number" && n >= 0 && (n | 0) === n && keys.length === n + 1) {
        var complete = true;
        var arr = [];
        for (var j = 0; j < n; j++) {
          if (!Object.prototype.hasOwnProperty.call(v, j)) { complete = false; break; }
          arr.push(fixArrayShapes(v[j]));
        }
        if (complete) return arr;
      }
      for (var k = 0; k < keys.length; k++) v[keys[k]] = fixArrayShapes(v[keys[k]]);
    }
    return v;
  }

  function unwrapRunCodePayload(wrapped) {
    var payload = wrapped;
    // $se may JSON-encode the (already stringified) runCode return again —
    // keep peeling string layers until an object/value comes out.
    var guard = 0;
    while (typeof payload === "string" && guard++ < 8) {
      try { payload = JSON.parse(payload); } catch (_) { break; }
    }

    // runCode returns a JSON string ({success, result, code}) — unwrap to
    // the userfunctions result. Older builds may hand back a Qt-bridged
    // object instead: property checks can fail on those, so detect the
    // envelope via for-in and read fields the same way.
    var inner;
    if (payload && typeof payload === "object") {
      var seen = {};
      try { for (var k in payload) seen[k] = true; } catch (_) {}
      if (seen.result && seen.success && seen.code) {
        inner = payload.result;
      }
    }
    if (inner !== undefined) {
      if (typeof inner === "string") {
        try { inner = JSON.parse(inner); } catch (_) {}
      }
      return fixArrayShapes(inner);
    }
    return fixArrayShapes(payload);
  }

  // Builds "return <fn>(<args>);" and ships it through PT's scripting host
  // via $se('runCode', ...). runCode() (in source/runcode.js) wraps the
  // return value as { success, result, code }.
  function executePTCode(funcName, args) {
    return new Promise(function (resolve, reject) {
      try {
        var argsStr = (args || []).map(serializePTArgument).join(", ");
        var wrapped = $se("runCode", "return " + funcName + "(" + argsStr + ");");
        resolve(unwrapRunCodePayload(wrapped));
      } catch (err) {
        reject(err);
      }
    });
  }

  // Maps tool_name -> ordered argument list for the JS function in
  // userfunctions.js. Keep aligned with mcp_server/tools.py.
  var TOOL_ARGS = {
    addDevice:           ["deviceName", "deviceModel", "x", "y"],
    addModule:           ["deviceName", "slot", "model"],
    addLink:             ["device1Name", "device1Interface",
                          "device2Name", "device2Interface", "linkType"],
    removeDevice:        ["deviceNames"],
    removeLink:          ["links"],
    configurePcIp:       ["deviceName", "dhcpEnabled", "ipaddress",
                          "subnetMask", "defaultGateway", "dnsServer"],
    configureIosDevice:  ["deviceName", "commands"],
    getNetwork:          [],
    getDeviceInfo:       ["deviceName"],
    setSimulationMode:   ["toSimMode"],
    getSimulationStatus: [],
    stepSimulation:      ["direction", "steps"],
    sendPdu:             ["sourceDevice", "destinationDevice"],
    renameDevice:        ["deviceName", "newName"],
    moveDevice:          ["deviceName", "x", "y"],
    setPower:            ["deviceName", "power"],
    getPduResults:       ["types"],
    getCommandLog:       ["deviceName", "limit"],
    startPcCommand:      ["deviceName", "command"],
    readPcOutput:        ["deviceName"],
    buildTopology:       ["devices", "links", "pcConfigs", "stopOnError"],
  };

  function buildPositionalArgs(tool, input) {
    var spec = TOOL_ARGS[tool];
    if (!spec) return null;
    var out = [];
    for (var i = 0; i < spec.length; i++) out.push(input[spec[i]]);
    return out;
  }

  function handleToolCall(socket, data) {
    data = data || {};
    var tool = data.tool_name;
    var args = data.tool_input || {};
    var tcid = data.tool_call_id;

    if (!tool || !tcid) {
      logLine("malformed tool_call", "err");
      return;
    }

    logLine("→ " + tool + " " + JSON.stringify(args).slice(0, 80));

    var positional = buildPositionalArgs(tool, args);
    if (!positional) {
      var unsupported = buildErrorResult(tool, args, "unsupported tool: " + tool);
      logLine("← " + tool + " err: unsupported tool", "err");
      emitToolResult(socket, tcid, tool, args, unsupported);
      return;
    }

    executePTCode(tool, positional)
      .then(function (result) {
        incrementToolCount();

        var ok = result && result.success !== false;
        logLine("← " + tool + (ok ? " ok" : " err: " + (result && result.error)), ok ? "ok" : "err");
        emitToolResult(socket, tcid, tool, args, result);
      })
      .catch(function (err) {
        incrementToolCount();

        var msg = (err && err.message) || String(err);
        logLine("← " + tool + " threw: " + msg, "err");
        emitToolResult(socket, tcid, tool, args, buildErrorResult(tool, args, msg));
      });
  }

  function bindSocketEvents(socket) {
    socket.on("connect", function () {
      setStatus("connected", "connected");
      if ($sid) $sid.textContent = socket.id;
      logLine("connected sid=" + socket.id, "ok");
    });

    socket.on("connect_error", function (err) {
      setStatus("offline", "offline");
      logLine("connect_error: " + ((err && err.message) || err), "err");
    });

    socket.on("disconnect", function (reason) {
      setStatus("connecting", "reconnecting");
      if ($sid) $sid.textContent = "—";
      logLine("disconnect: " + reason, "err");
    });

    socket.on("tool_call", function (data) {
      handleToolCall(socket, data);
    });
  }

  function createSocket() {
    return io(MCP_URL, {
      transports: ["websocket"],
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
    });
  }

  setStatus("connecting", "connecting");
  logLine("connecting to " + MCP_URL);

  var socket = createSocket();
  bindSocketEvents(socket);
})();
