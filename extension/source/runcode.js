// PT-side runCode — evaluates a JS string and returns a {success, result, code} envelope.
// Wrap in an IIFE so "return ..." from the webview is valid, and use eval so
// the Script Module sandbox can see userfunctions.js globals (PT 8.0's
// new Function cannot).

function runCode(scriptText) {
  try {
    var result = eval("(function(){\n" + scriptText + "\n})()");
    // Return a JSON string: PT's $se bridge mangles object returns —
    // props may be invisible to JSON.stringify / `in` / dot access.
    return JSON.stringify({ success: true, result: result, code: scriptText });
  } catch (error) {
    return JSON.stringify({
      success: false,
      error: "Execution error: " + (error.message || String(error)),
      errorType: error.name || "Error",
      lineNumber: error.lineNumber,
      stack: error.stack,
      code: scriptText,
    });
  }
}
