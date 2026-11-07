const byId = id => document.getElementById(id);
let refreshing = false;

function node(tag, value = "") {
  const result = document.createElement(tag);
  result.textContent = value;
  return result;
}

async function request(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the input and try again.");
  return data;
}

function options(id, entries) {
  const select = byId(id);
  const previous = select.value;
  select.replaceChildren(...entries.map(([value, label]) => new Option(label, value)));
  if (entries.some(([value]) => String(value) === previous)) select.value = previous;
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const [datasets, targets, runs] = await Promise.all([
      request("/datasets"), request("/targets"), request("/runs")
    ]);
    options("dataset", datasets.map(item => [item.version, item.version.slice(0, 8) + " · " + item.total + " cases"]));
    options("target", targets.map(target => [target, target]));
    const label = run => "#" + run.id + " · " + run.target;
    const complete = runs.filter(run => run.status === "completed").map(run => [run.id, label(run)]);
    options("baseline", complete);
    options("candidate", complete);
    byId("runs").replaceChildren(...runs.map(run =>
      node("li", label(run) + " · " + run.status + " · " + run.progress + "/" + run.total +
        (run.error ? " · " + run.error : ""))
    ));
    if (!runs.length) byId("runs").append(node("li", "No runs yet."));
  } finally {
    refreshing = false;
  }
}

function form(id, action) {
  const form = byId(id);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    byId("notice").textContent = "";
    button.disabled = true;
    try {
      await action();
    } catch (error) {
      byId("notice").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

function caseDetails(label, result) {
  const card = node("div");
  card.append(node("h3", label), node("p", result.error || result.answer?.answer || "Abstained"));
  card.append(node("p", "Citations: " + (result.answer?.citations.map(item => item.title).join(", ") || "None")));
  const passages = node("ol");
  for (const hit of result.search) passages.append(node("li", hit.title + " · " + hit.text));
  card.append(node("p", "Retrieved passages"), passages);
  return card;
}

function render(before, after, decision) {
  const panel = byId("comparison");
  const outcome = node("h3", decision.passed ? "Release gate passed" : "Release gate failed");
  outcome.className = decision.passed ? "pass" : "fail";
  panel.replaceChildren(outcome);
  const reasons = node("ul");
  for (const reason of decision.failures) reasons.append(node("li", reason));
  if (decision.failures.length) panel.append(reasons);
  panel.append(node("p", decision.regressions.length + " newly failing cases"));
  const table = node("table");
  const header = node("tr");
  for (const label of ["Metric", "Baseline", "Candidate"]) header.append(node("th", label));
  table.append(header);
  for (const [key, label] of [
    ["retrieval_hit", "Retrieval hit@5"], ["citation_correct", "Citation match"],
    ["abstention_correct", "Abstention"], ["p95_ms", "p95 probe latency"], ["errors", "Probe errors"]
  ]) {
    const format = value => key === "p95_ms" ? value + " ms" : key === "errors" ? String(value) : (value * 100).toFixed(1) + "%";
    const row = node("tr");
    row.append(node("td", label), node("td", format(before.summary[key])), node("td", format(after.summary[key])));
    table.append(row);
  }
  panel.append(table);
  const baselineCases = new Map(before.cases.map(item => [item.id, item]));
  const regressions = new Set(decision.regressions);
  const cases = [...after.cases].sort((a, b) => Number(regressions.has(b.id)) - Number(regressions.has(a.id)));
  for (const result of cases) {
    const details = node("details");
    details.append(node("summary", (regressions.has(result.id) ? "Regression · " : "") + result.id + " · " + result.question));
    details.append(node("p", "Expected: " + (result.expected_sources.join(", ") || "Abstention")));
    const pair = node("div");
    pair.className = "pair";
    pair.append(caseDetails("Baseline", baselineCases.get(result.id)), caseDetails("Candidate", result));
    details.append(pair);
    panel.append(details);
  }
}

form("queue", async () => {
  const run = await request("/runs", { dataset_version: byId("dataset").value, target: byId("target").value });
  byId("notice").textContent = "Queued run #" + run.id;
  await refresh();
});

form("import", async () => {
  const file = byId("file").files[0];
  if (!file || file.size > 250000) throw new Error("Select a JSON file under 250 KB.");
  const data = JSON.parse(await file.text());
  const imported = await request("/datasets", Array.isArray(data) ? { cases: data } : data);
  byId("notice").textContent = "Imported " + imported.total + " cases";
  await refresh();
});

form("compare", async () => {
  byId("comparison").replaceChildren();
  const baseline = byId("baseline").value;
  const candidate = byId("candidate").value;
  const [decision, before, after] = await Promise.all([
    request("/comparison?baseline=" + baseline + "&candidate=" + candidate),
    request("/runs/" + baseline), request("/runs/" + candidate)
  ]);
  render(before.report, after.report, decision);
});

refresh().catch(error => { byId("notice").textContent = error.message; });
setInterval(() => refresh().catch(error => { byId("notice").textContent = error.message; }), 2500);
