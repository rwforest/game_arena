# Minimal FastAPI app to run on Databricks Apps for game_arena
from __future__ import annotations
from typing import Literal, Optional, Any, Dict
import os
import uuid
import urllib.parse

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from game_arena.harness import tournament_util
from game_arena.harness import model_generation_sdk
from game_arena.harness import model_generation_http
from game_arena.harness import game_notation_examples, prompt_generation, prompts, parsers
import pyspiel
import mlflow
from app.chess_logging import ChessRecorder
from app import db_sql
import requests

# Optional MLflow GenAI Tracing (no-op if not available)
try:
    from mlflow.tracing import start_trace, start_span
    _HAS_TRACING = True
except Exception:
    start_trace = start_span = None
    _HAS_TRACING = False

app = FastAPI(title="game_arena API", version="0.1.3")

class GenerateRequest(BaseModel):
    provider: Literal["openai", "gemini", "anthropic", "together", "xai"] = "openai"
    model_name: str
    prompt: str
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 1024
    stream: Optional[bool] = False
    timeout: Optional[int] = 600
    base_url: Optional[str] = None  # for AI Gateway per-request override


def _build_model(req: 'GenerateRequest'):
    # Prefer Databricks AI Gateway/OpenAI-compatible endpoint if provided via request or environment
    gateway_base_url = (
        req.base_url
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("DATABRICKS_AI_GATEWAY_BASE_URL")
        or None
    )
    if not gateway_base_url:
        # Fall back to the first Databricks Serving/Gateway route if available
        default_route = _get_default_gateway_route()
        if default_route:
            gateway_base_url = default_route.get("base_url")

    opts: Dict[str, Any] = {"temperature": req.temperature, "max_output_tokens": req.max_tokens}
    api: Dict[str, Any] = {"stream": bool(req.stream), "timeout": req.timeout}

    if gateway_base_url:
        # Route through OpenAI-compatible API no matter the selected provider
        api["base_url"] = gateway_base_url
        return model_generation_sdk.OpenAIChatCompletionsModel(
            model_name=req.model_name, model_options=opts, api_options=api
        )

    # Fall back to native provider SDKs
    if req.provider == "openai":
        return model_generation_sdk.OpenAIChatCompletionsModel(
            model_name=req.model_name, model_options=opts, api_options=api
        )
    if req.provider == "gemini":
        return model_generation_sdk.AIStudioModel(
            model_name=req.model_name, model_options=opts, api_options=api
        )
    if req.provider == "anthropic":
        # Map max_tokens to Anthropic if provided
        aopts = {k: v for k, v in opts.items() if v is not None}
        if "max_output_tokens" in aopts:
            aopts["max_tokens"] = aopts.pop("max_output_tokens")
        return model_generation_sdk.AnthropicModel(
            model_name=req.model_name, model_options=aopts, api_options=api
        )
    if req.provider == "together":
        return model_generation_http.TogetherAIModel(
            model_name=req.model_name, model_options=opts, api_options=api
        )
    if req.provider == "xai":
        return model_generation_http.XAIModel(
            model_name=req.model_name, model_options=opts, api_options=api
        )
    raise ValueError(f"Unsupported provider {req.provider}")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/generate")
def generate(req: GenerateRequest):
    mi = tournament_util.ModelTextInput(prompt_text=req.prompt)

    trace_cm = start_trace("game_arena_app_call") if _HAS_TRACING else None
    try:
        if trace_cm:
            trace_ctx = trace_cm.__enter__()
        # per-request span
        span_cm = start_span(f"{req.provider}.generate") if _HAS_TRACING else None
        try:
            if span_cm:
                span = span_cm.__enter__()
                if hasattr(span, "set_inputs"):
                    span.set_inputs({"prompt": req.prompt, "provider": req.provider, "model_name": req.model_name})
            model = _build_model(req)
            ret = model.generate_with_text_input(mi)
            out: Dict[str, Any] = {
                "text": ret.main_response,
                "usage": {
                    "prompt_tokens": ret.prompt_tokens,
                    "generation_tokens": ret.generation_tokens,
                    "reasoning_tokens": getattr(ret, "reasoning_tokens", None),
                },
                "provider": req.provider,
                "model_name": req.model_name,
            }
            if span_cm and hasattr(span, "set_outputs"):
                span.set_outputs({
                    "text": ret.main_response,
                    # Thoughts are redacted by default. Only log if approved.
                    "request": ret.request_for_logging,
                    "response": ret.response_for_logging,
                    "usage": out["usage"],
                })
            return out
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if span_cm:
                span_cm.__exit__(None, None, None)
    finally:
        if trace_cm:
            trace_cm.__exit__(None, None, None)


class PlayerConfig(BaseModel):
    provider: Literal["openai", "gemini", "anthropic", "together", "xai"]
    model_name: str
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 1024
    timeout: Optional[int] = 600
    base_url: Optional[str] = None  # if set, route via Gateway with OpenAI wrapper


class ChessGameRequest(BaseModel):
    num_moves: int = 6
    player_white: PlayerConfig
    player_black: PlayerConfig
    log_thoughts: bool = False  # default redact


@app.post("/game/chess/start")
def start_chess_game(req: ChessGameRequest):
    # Ensure SQL table exists (no-op if connector not installed)
    try:
        db_sql.init_table()
    except Exception:
        pass

    # Unique experiment per game + run
    game_id = str(uuid.uuid4())
    exp_name = f"game_arena_game_{game_id}"
    mlflow.set_experiment(exp_name)

    # Optionally fetch available routes to default the players
    routes = _fetch_gateway_routes()
    route_w = routes[0] if len(routes) > 0 else None
    route_b = routes[1] if len(routes) > 1 else route_w

    # Create an MLflow run to attach logs/artifacts
    run_ctx = mlflow.start_run(run_name=game_id)
    try:
        active = mlflow.active_run()
        run_id = active.info.run_id if active else None
        # Log basic params for reproducibility
        try:
            mlflow.log_params({
                "game": "chess",
                "num_moves": req.num_moves,
                "white_provider": req.player_white.provider,
                "white_model": req.player_white.model_name,
                "black_provider": req.player_black.provider,
                "black_model": req.player_black.model_name,
            })
        except Exception:
            pass

        # Set up game and helpers
        game = pyspiel.load_game("chess")
        state = game.new_initial_state()
        prompt_gen = prompt_generation.PromptGeneratorText()
        prompt_template = prompts.PromptTemplate.NO_LEGAL_ACTIONS
        parser = parsers.ChainedMoveParser([parsers.RuleBasedMoveParser(), parsers.SoftMoveParser("chess")])

        # Build models for both players with native or Gateway based on base_url/env
        def build_from_player(pc: PlayerConfig, default_route: Optional[dict]):
            base_url = pc.base_url or (default_route.get("base_url") if default_route else None)
            model_name = pc.model_name
            if (not model_name or model_name.lower() == "auto") and default_route:
                model_name = default_route.get("model") or default_route.get("name") or model_name
            greq = GenerateRequest(
                provider=pc.provider,
                model_name=model_name,
                prompt="",  # unused placeholder
                temperature=pc.temperature,
                max_tokens=pc.max_tokens,
                stream=False,
                timeout=pc.timeout,
                base_url=base_url,
            )
            return _build_model(greq)

        model_white = build_from_player(req.player_white, route_w)
        model_black = build_from_player(req.player_black, route_b)

        # Recorder for PGN
        rec = ChessRecorder(headers={"Event": "game_arena", "Site": "Databricks App", "Run": str(run_id) if run_id else ""})

        with start_trace("chess_game") if _HAS_TRACING else _nullcontext():  # type: ignore
            for move_number in range(req.num_moves):
                if state.is_terminal():
                    break
                # Build prompt
                subs = {
                    "readable_state_str": tournament_util.convert_to_readable_state(
                        game_short_name="chess",
                        state_str=state.to_string(),
                        current_player=state.current_player(),
                    ),
                    "move_history": (tournament_util.get_action_string_history(state) or "None"),
                    "player_name": game_notation_examples.GAME_SPECIFIC_NOTATIONS["chess"][
                        "player_map"
                    ][state.current_player()],
                    "move_notation": game_notation_examples.GAME_SPECIFIC_NOTATIONS["chess"][
                        "move_notation"
                    ],
                    "notation": game_notation_examples.GAME_SPECIFIC_NOTATIONS["chess"][
                        "state_notation"
                    ],
                }
                prompt = prompt_gen.generate_prompt_with_text_only(
                    prompt_template=prompt_template, game_short_name="chess", **subs
                )
                mi = tournament_util.ModelTextInput(prompt_text=prompt.prompt_text)

                # Pick model per side
                model = model_white if state.current_player() == 0 else model_black

                # Span for model call
                span_cm = start_span("model.generate") if _HAS_TRACING else _nullcontext()
                try:
                    span = span_cm.__enter__()
                    if hasattr(span, "set_inputs"):
                        span.set_inputs({
                            "player": state.current_player(),
                            "prompt": mi.prompt_text,
                        })
                    ret = model.generate_with_text_input(mi)
                    if hasattr(span, "set_outputs"):
                        outputs = {
                            "text": ret.main_response,
                            "request": ret.request_for_logging,
                            "response": ret.response_for_logging,
                            "prompt_tokens": ret.prompt_tokens,
                            "generation_tokens": ret.generation_tokens,
                            "reasoning_tokens": getattr(ret, "reasoning_tokens", None),
                        }
                        if req.log_thoughts:
                            outputs["chain_of_thought"] = ret.main_response_and_thoughts
                        span.set_outputs(outputs)
                finally:
                    span_cm.__exit__(None, None, None)

                # Parse
                pin = parsers.TextParserInput(
                    text=ret.main_response,
                    state_str=state.to_string(),
                    legal_moves=parsers.get_legal_action_strings(state),
                    player_number=state.current_player(),
                )
                move = parser.parse(pin)
                if not move:
                    break
                # Record and apply move
                rec.push_san(move)
                state.apply_action(state.string_to_action(move))

        # Finalize PGN
        pgn_game = tournament_util.get_pgn(state)
        pgn_str = str(pgn_game)
        result = pgn_game.headers.get("Result", "*")
        paths = rec.finalize(result_str=result)

        # Attach to MLflow run
        try:
            mlflow.log_text(pgn_str, "game.pgn")
        except Exception:
            pass

        # Insert to SQL table if connector available
        try:
            experiment = mlflow.get_experiment_by_name(exp_name)
            experiment_id = experiment.experiment_id if experiment else None
        except Exception:
            experiment_id = None
        try:
            db_sql.insert_game(
                experiment_id=str(experiment_id),
                run_id=str(run_id),
                white="White",
                black="Black",
                model_white=req.player_white.model_name,
                model_black=req.player_black.model_name,
                result=result,
                pgn=pgn_str,
            )
        except Exception:
            pass

        return {
            "experiment_name": exp_name,
            "experiment_id": str(experiment_id) if experiment_id else None,
            "run_id": run_id,
            "result": result,
            "pgn": pgn_str,
            "artifacts": paths,
        }
    finally:
        try:
            mlflow.end_run()
        except Exception:
            pass


# --- Databricks discovery helpers ---

def _workspace_url() -> Optional[str]:
    ws = os.environ.get("DATABRICKS_WORKSPACE_URL")
    if ws:
        return ws.rstrip("/")
    host = os.environ.get("DATABRICKS_HOST")
    if host:
        if not host.startswith("http://") and not host.startswith("https://"):
            host = "https://" + host
        return host.rstrip("/")
    return None


def _auth_headers() -> Dict[str, str]:
    token = os.environ.get("DATABRICKS_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _fetch_routes_via_sdk() -> list[dict]:
    """Discover AI Gateway routes and Serving Endpoints using databricks-sdk.

    Returns a list of {name, provider?, model?, base_url} entries suitable for UI/model selection.
    """
    ws = _workspace_url()
    if not ws:
        return []

    routes: list[dict] = []
    try:
        # Import and construct client using env (or explicit host/token if available)
        from databricks.sdk import WorkspaceClient  # type: ignore
        token = os.environ.get("DATABRICKS_TOKEN")
        w = WorkspaceClient(host=ws, token=token) if token else WorkspaceClient(host=ws)

        # 1) Try AI Gateway route listing
        try:
            ag = getattr(w, "ai_gateway", None)
            if ag is not None:
                # Prefer flat list method if present
                if hasattr(ag, "list_routes"):
                    resp = ag.list_routes()  # could be iterator or response object
                    items = getattr(resp, "routes", None) or getattr(resp, "data", None) or resp
                elif hasattr(ag, "routes") and hasattr(ag.routes, "list"):
                    resp = ag.routes.list()
                    items = getattr(resp, "routes", None) or getattr(resp, "data", None) or resp
                else:
                    items = []
                for r in items or []:
                    # r could be dict-like or object; use getattr/keys defensively
                    name = getattr(r, "name", None) or getattr(r, "route_name", None) or (r.get("name") if isinstance(r, dict) else None) or (r.get("route_name") if isinstance(r, dict) else None)
                    if not name:
                        continue
                    target = getattr(r, "target", None) or (r.get("target") if isinstance(r, dict) else None)
                    provider = None
                    model = None
                    if target is not None:
                        provider = getattr(target, "provider", None) or getattr(target, "vendor", None) or (target.get("provider") if isinstance(target, dict) else None) or (target.get("vendor") if isinstance(target, dict) else None)
                        model = getattr(target, "model", None) or getattr(target, "model_name", None) or (target.get("model") if isinstance(target, dict) else None) or (target.get("model_name") if isinstance(target, dict) else None)
                    base_url = f"{ws}/serving-endpoints/{name}/openai/v1"
                    routes.append({"name": name, "provider": provider, "model": model, "base_url": base_url})
        except Exception:
            # Ignore AI Gateway failures and continue with Serving Endpoints
            pass

        # 2) List Serving Endpoints as fallback/additional entries
        try:
            se = getattr(w, "serving_endpoints", None)
            if se is not None and hasattr(se, "list"):
                eps = se.list()
                for ep in eps or []:
                    name = getattr(ep, "name", None) or (ep.get("name") if isinstance(ep, dict) else None)
                    if not name:
                        continue
                    base_url = f"{ws}/serving-endpoints/{name}/openai/v1"
                    # Avoid duplicate from AI Gateway if same name already added
                    if not any(r.get("name") == name for r in routes):
                        routes.append({"name": name, "provider": None, "model": None, "base_url": base_url})
        except Exception:
            pass

        return routes
    except Exception:
        return []


def _fetch_gateway_routes() -> list[dict]:
    # Prefer official SDK; fall back to REST if SDK/permissions are unavailable
    sdk_routes = _fetch_routes_via_sdk()
    if sdk_routes:
        return sdk_routes

    ws = _workspace_url()
    if not ws:
        return []
    url = f"{ws}/api/2.0/ai-gateway/routes"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        routes = data.get("routes") or data.get("data") or []
        out = []
        for r in routes:
            name = r.get("name") or r.get("route_name") or r.get("id")
            target = r.get("target") or {}
            provider = target.get("provider") or target.get("vendor")
            model = target.get("model") or target.get("model_name")
            base_url = f"{ws}/serving-endpoints/{name}/openai/v1"
            out.append({"name": name, "provider": provider, "model": model, "base_url": base_url})
        return out
    except Exception:
        # As a last resort, list Serving Endpoints via REST
        try:
            url2 = f"{ws}/api/2.0/serving-endpoints"
            resp2 = requests.get(url2, headers=_auth_headers(), timeout=15)
            resp2.raise_for_status()
            data2 = resp2.json()
            items = data2.get("endpoints") or data2.get("serving_endpoints") or []
            out2 = []
            for ep in items:
                name = ep.get("name")
                if not name:
                    continue
                base_url = f"{ws}/serving-endpoints/{name}/openai/v1"
                out2.append({"name": name, "provider": None, "model": None, "base_url": base_url})
            return out2
        except Exception:
            return []


def _get_default_gateway_route() -> Optional[dict]:
    routes = _fetch_gateway_routes()
    return routes[0] if routes else None


@app.get("/databricks/models")
def list_databricks_models():
    """List models/endpoints using Databricks SDK (fallback to REST) to populate the UI.

    Returns: { gateway_routes: [ {name, provider?, model?, base_url} ] }
    """
    routes = _fetch_gateway_routes()
    return {"gateway_routes": routes}


INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>game_arena UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:20px;max-width:1000px}
    label{display:block;margin:8px 0 4px}
    input,select,textarea,button{font:inherit}
    textarea{width:100%;min-height:120px}
    .row{display:flex;gap:12px;flex-wrap:wrap}
    .row > div{flex:1 1 220px}
    .out{white-space:pre-wrap;background:#f6f8fa;border:1px solid #ddd;padding:10px;border-radius:6px}
    .small{font-size:12px;color:#555}
  </style>
</head>
<body>
  <h1>game_arena – Minimal UI</h1>
  <div class="row">
    <div>
      <label>Provider</label>
      <select id="provider">
        <option value="openai">openai (incl. AI Gateway)</option>
        <option value="gemini">gemini</option>
        <option value="anthropic">anthropic</option>
        <option value="together">together</option>
        <option value="xai">xai</option>
      </select>
    </div>
    <div>
      <label>Model name</label>
      <input id="model" placeholder="e.g., gpt-4o-mini or claude-3-7-sonnet-20250219" size="40" />
      <div class="small">Tip: Use Databricks Gateway routes via the button below.</div>
      <button id="load_routes_gen">Load routes from Databricks</button>
      <select id="route_gen" style="display:none"></select>
    </div>
    <div>
      <label>Temperature</label>
      <input id="temp" type="number" step="0.01" value="0.2" />
    </div>
    <div>
      <label>Max tokens</label>
      <input id="max" type="number" value="512" />
    </div>
  </div>
  <label>Prompt</label>
  <textarea id="prompt" placeholder="Type your prompt..."></textarea>
  <div style="margin-top:10px">
    <label><input id="stream" type="checkbox" /> Stream</label>
    <input id="base_url" placeholder="Optional OpenAI base_url (AI Gateway)" size="60" />
    <button id="run">Generate</button>
  </div>
  <h3>Output</h3>
  <div id="output" class="out"></div>
  <h4>Usage</h4>
  <div id="usage" class="out"></div>

  <hr />
  <h2>Start Chess Game</h2>
  <div class="row">
    <div><strong>White</strong></div>
    <div>
      <label>Provider</label>
      <select id="w_provider">
        <option value="gemini">gemini</option>
        <option value="openai">openai</option>
        <option value="anthropic">anthropic</option>
        <option value="together">together</option>
        <option value="xai">xai</option>
      </select>
    </div>
    <div>
      <label>Model</label>
      <input id="w_model" placeholder="gemini-2.5-flash" />
      <button id="load_routes_w">Load routes</button>
      <select id="route_w" style="display:none"></select>
    </div>
    <div>
      <label>Gateway base_url (optional)</label>
      <input id="w_baseurl" placeholder="https://.../v1" size="40" />
    </div>
  </div>
  <div class="row">
    <div><strong>Black</strong></div>
    <div>
      <label>Provider</label>
      <select id="b_provider">
        <option value="openai">openai</option>
        <option value="gemini">gemini</option>
        <option value="anthropic">anthropic</option>
        <option value="together">together</option>
        <option value="xai">xai</option>
      </select>
    </div>
    <div>
      <label>Model</label>
      <input id="b_model" placeholder="gpt-4o-mini" />
      <button id="load_routes_b">Load routes</button>
      <select id="route_b" style="display:none"></select>
    </div>
    <div>
      <label>Gateway base_url (optional)</label>
      <input id="b_baseurl" placeholder="https://.../v1" size="40" />
    </div>
  </div>
  <div class="row">
    <div>
      <label>Moves</label>
      <input id="moves" type="number" value="6" />
    </div>
    <div>
      <label><input id="log_thoughts" type="checkbox" /> Log chain-of-thought (if available)</label>
    </div>
    <div>
      <button id="start_game">Start game</button>
    </div>
  </div>
  <h4>Game Result</h4>
  <div id="game_res" class="out"></div>

<script>
async function fetchRoutes() {
  const r = await fetch('/databricks/models');
  if (!r.ok) throw new Error('Failed to fetch routes');
  const j = await r.json();
  return j.gateway_routes || [];
}
function populateRouteSelect(sel, routes) {
  sel.innerHTML = '';
  routes.forEach(rt => {
    const opt = document.createElement('option');
    opt.value = JSON.stringify(rt);
    const label = rt.name + (rt.model ? (' – ' + rt.model) : '') + (rt.provider ? (' [' + rt.provider + ']') : '');
    opt.textContent = label;
    sel.appendChild(opt);
  });
  sel.style.display = routes.length ? '' : 'none';
}
function applyRouteTo(prefix, rt) {
  // Use OpenAI-compatible Gateway path for base_url; default provider to openai
  document.getElementById(prefix + '_provider').value = 'openai';
  document.getElementById(prefix + '_model').value = rt.model || rt.name || '';
  const baseField = document.getElementById(prefix + '_baseurl');
  if (baseField) baseField.value = rt.base_url || '';
}
async function onLoadRoutes(prefix) {
  try {
    const routes = await fetchRoutes();
    const sel = document.getElementById('route_' + prefix);
    populateRouteSelect(sel, routes);
    sel.onchange = () => {
      const rt = JSON.parse(sel.value);
      applyRouteTo(prefix, rt);
    };
    if (routes.length) {
      // Auto-apply first route
      applyRouteTo(prefix, routes[0]);
    }
  } catch (e) {
    alert('Error loading routes: ' + e.message);
  }
}
async function onLoadRoutesGen() {
  try {
    const routes = await fetchRoutes();
    const sel = document.getElementById('route_gen');
    populateRouteSelect(sel, routes);
    sel.onchange = () => {
      const rt = JSON.parse(sel.value);
      document.getElementById('provider').value = 'openai';
      document.getElementById('model').value = rt.model || rt.name || '';
      document.getElementById('base_url').value = rt.base_url || '';
    };
    if (routes.length) {
      const rt = routes[0];
      document.getElementById('provider').value = 'openai';
      document.getElementById('model').value = rt.model || rt.name || '';
      document.getElementById('base_url').value = rt.base_url || '';
    }
  } catch (e) {
    alert('Error loading routes: ' + e.message);
  }
}

async function generate() {
  const body = {
    provider: document.getElementById('provider').value,
    model_name: document.getElementById('model').value || 'gpt-4o-mini',
    prompt: document.getElementById('prompt').value,
    temperature: parseFloat(document.getElementById('temp').value || '0.2'),
    max_tokens: parseInt(document.getElementById('max').value || '512'),
    stream: document.getElementById('stream').checked,
    base_url: document.getElementById('base_url').value || null,
  };
  const out = document.getElementById('output');
  const usage = document.getElementById('usage');
  out.textContent = 'Running...';
  usage.textContent = '';

  try {
    const resp = await fetch('/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    if (!resp.ok) {
      const err = await resp.json().catch(()=>({detail: resp.statusText}));
      throw new Error(err.detail || ('HTTP ' + resp.status));
    }
    const data = await resp.json();
    out.textContent = data.text || '';
    usage.textContent = JSON.stringify(data.usage || {}, null, 2);
  } catch (e) {
    out.textContent = 'Error: ' + e.message;
  }
}
async function startGame() {
  const body = {
    num_moves: parseInt(document.getElementById('moves').value || '6'),
    log_thoughts: document.getElementById('log_thoughts').checked,
    player_white: {
      provider: document.getElementById('w_provider').value,
      model_name: document.getElementById('w_model').value || 'gemini-2.5-flash',
      base_url: document.getElementById('w_baseurl').value || null
    },
    player_black: {
      provider: document.getElementById('b_provider').value,
      model_name: document.getElementById('b_model').value || 'gpt-4o-mini',
      base_url: document.getElementById('b_baseurl').value || null
    }
  };
  const box = document.getElementById('game_res');
  box.textContent = 'Running game...';
  try {
    const resp = await fetch('/game/chess/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (!resp.ok) {
      const err = await resp.json().catch(()=>({detail: resp.statusText}));
      throw new Error(err.detail || ('HTTP ' + resp.status));
    }
    const data = await resp.json();
    box.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    box.textContent = 'Error: ' + e.message;
  }
}
window.addEventListener('DOMContentLoaded', ()=>{
  document.getElementById('run').addEventListener('click', generate);
  document.getElementById('start_game').addEventListener('click', startGame);
  document.getElementById('load_routes_w').addEventListener('click', ()=> onLoadRoutes('w'));
  document.getElementById('load_routes_b').addEventListener('click', ()=> onLoadRoutes('b'));
  document.getElementById('load_routes_gen').addEventListener('click', onLoadRoutesGen);
  // Auto-load routes on page load and prefill defaults
  onLoadRoutes('w');
  onLoadRoutes('b');
  onLoadRoutesGen();
});
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index_page():
    return INDEX_HTML

# Simple endpoints to serve saved games (if any saved by separate flow)
@app.get("/games/latest.pgn", response_class=PlainTextResponse)
def latest_pgn():
    out_dir = os.environ.get("GA_CHESS_OUT_DIR", "runs/games")
    try:
        import glob
        files = sorted(glob.glob(os.path.join(out_dir, "*.pgn")))
        if not files:
            return ""
        with open(files[-1], "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

# Fallback nullcontext when tracing is not available
class _nullcontext:
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False