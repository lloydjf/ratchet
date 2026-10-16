// Outcome words and decisions written as code in a table cell become small colour tags.
// The word stays the text, so colour is never the only carrier; without this file the
// tables read the same, just uncoloured.
(function () {
  "use strict";
  var KIND = {
    pass: "pass",
    improved: "pass",
    PASS: "pass",
    drift: "drift",
    regression: "block",
    BLOCK: "block",
    uncertain: "warn",
    WARN: "warn",
    failed: "neutral",
  };
  // Columns that name fields or settings, where `drift` or `pass` is an identifier.
  var NAME_COLUMN = /^(field|name|setting|variable|key|code|parameter)$/i;
  // Cells listing judging stages (`level0` | `nano` | … | `drift`) are not outcomes.
  var STAGES = ["level0", "nano", "super", "ultra", "control", "infra"];

  function headerText(cell) {
    var table = cell.closest("table");
    var row = table && table.tHead && table.tHead.rows[0];
    var head = row && row.cells[cell.cellIndex];
    return head ? head.textContent.trim() : "";
  }

  function isStageList(cell) {
    var codes = cell.querySelectorAll("code");
    for (var i = 0; i < codes.length; i++) {
      if (STAGES.indexOf(codes[i].textContent.trim()) !== -1) return true;
    }
    return false;
  }

  var codes = document.querySelectorAll(".md-typeset table td code");
  for (var i = 0; i < codes.length; i++) {
    var code = codes[i];
    var kind = KIND[code.textContent.trim()];
    if (!kind || code.closest("a, pre")) continue;
    var cell = code.closest("td");
    if (NAME_COLUMN.test(headerText(cell)) || isStageList(cell)) continue;
    code.classList.add("r-tag", "r-tag--" + kind);
  }
})();
