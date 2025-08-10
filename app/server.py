# Minimal FastAPI app to run on Databricks Apps for game_arena
from __future__ import annotations
from typing import Literal, Optional, Any, Dict
import os
import uuid
import urllib.parse
from contextlib import nullcontext as _nullcontext
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
app = FastAPI(title="game_arena API", version="0.1.4")
class GenerateRequest(BaseModel):
    provider: Literal["databricks", "openai", "gemini", "anthropic", "together", "xai"] = "databricks"
    model_name: str
    prompt: str
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 1024
    stream: Optional[bool] = False
    timeout: Optional[int] = 600
    base_url: Optional[str] = None  # for AI Gateway per-request override

def _build_model(req: 'GenerateRequest'):
    # Prefer Databricks Serving via SDK by default
    opts: Dict[str, Any] = {"temperature": req.temperature, "max_output_tokens": req.max_tokens}
    api: Dict[str, Any] = {"stream": bool(req.stream), "timeout": req.timeout}
    if req.provider == "databricks":
        # If endpoint name not provided or is 'auto', choose first discovered
        endpoint_name = req.model_name
        if not endpoint_name or endpoint_name.lower() == "auto":
            default_route = _get_default_gateway_route()
            if default_route:
                endpoint_name = default_route.get("name") or default_route.get("id") or endpoint_name
        if not endpoint_name:
            raise HTTPException(status_code=400, detail="Databricks provider requires a serving endpoint name")
        return model_generation_sdk.DatabricksServingModel(
            model_name=endpoint_name,
            model_options=opts,
            api_options=api,
        )
    # Non-databricks providers: Prefer Databricks AI Gateway/OpenAI-compatible endpoint if provided via request or environment
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
    provider: Literal["databricks", "openai", "gemini", "anthropic", "together", "xai"]
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
        # Build models for both players with native or Gateway/SDK based on provider/base_url/env
        def build_from_player(pc: PlayerConfig, default_route: Optional[dict]):
            # For databricks provider always use SDK; pick endpoint by name or from default route
            if pc.provider == "databricks":
                endpoint_name = pc.model_name
                if (not endpoint_name or endpoint_name.lower() == "auto") and default_route:
                    endpoint_name = default_route.get("name") or default_route.get("id")
                greq = GenerateRequest(
                    provider="databricks",
                    model_name=endpoint_name or "",
                    prompt="",  # unused placeholder
                    temperature=pc.temperature,
                    max_tokens=pc.max_tokens,
                    stream=False,
                    timeout=pc.timeout,
                )
                return _build_model(greq)
            base_url = pc.base_url or (default_route.get("base_url") if default_route else None)
            model_name = pc.model_name
            if (not model_name or model_name.lower() == "auto") and default_route:
                # For non-databricks providers prefer the model id if present
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
        rec = ChessRecorder(headers={"Event": "game_arena", "Site": "Databricks App", "Run": str(run_id) if run_id else ""}, write_files=False)
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
        out_paths = rec.finalize(result_str=result)
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
            "artifacts": out_paths,
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
                    name = (
                        getattr(r, "name", None)
                        or getattr(r, "route_name", None)
                        or (r.get("name") if isinstance(r, dict) else None)
                        or (r.get("route_name") if isinstance(r, dict) else None)
                    )
                    if not name:
                        continue
                    target = getattr(r, "target", None) or (r.get("target") if isinstance(r, dict) else None)
                    provider = None
                    model = None
                    if target is not None:
                        provider = (
                            getattr(target, "provider", None)
                            or getattr(target, "vendor", None)
                            or (target.get("provider") if isinstance(target, dict) else None)
                            or (target.get("vendor") if isinstance(target, dict) else None)
                        )
                        model = (
                            getattr(target, "model", None)
                            or getattr(target, "model_name", None)
                            or (target.get("model") if isinstance(target, dict) else None)
                            or (target.get("model_name") if isinstance(target, dict) else None)
                        )
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
                    name = (
                        getattr(ep, "name", None)
                        or (ep.get("name") if isinstance(ep, dict) else None)
                    )
                    if not name:
                        continue
                    base_url = f"{ws}/serving-endpoints/{name}/openai/v1"
                    routes.append({
                        "name": name,
                        "provider": "databricks",
                        "model": name,
                        "base_url": base_url,
                    })
        except Exception:
            pass
    except Exception:
        # SDK may not be installed or env not configured
        return []
    return routes

def _fetch_gateway_routes() -> list[dict]:
    """Fetch available routes/endpoints using SDK first, then REST fallback."""
    routes = _fetch_routes_via_sdk()
    if routes:
        return routes
    ws = _workspace_url()
    if not ws:
        return []
    headers = _auth_headers()
    out: list[dict] = []
    # 1) AI Gateway via REST
    try:
        r = requests.get(f"{ws}/api/2.0/ai-gateway/routes", headers=headers, timeout=10)
        if r.ok:
            data = r.json()
            items = data.get("routes") if isinstance(data, dict) else data
            for it in items or []:
                name = it.get("name") or it.get("route_name")
                if not name:
                    continue
                target = it.get("target") or {}
                provider = target.get("provider") or target.get("vendor")
                model = target.get("model") or target.get("model_name")
                base_url = f"{ws}/serving-endpoints/{name}/openai/v1"
                out.append({"name": name, "provider": provider, "model": model, "base_url": base_url})
    except Exception:
        pass
    # 2) Serving Endpoints via REST
    try:
        r = requests.get(f"{ws}/api/2.0/serving-endpoints", headers=headers, timeout=10)
        if r.ok:
            data = r.json()
            items = data.get("endpoints") if isinstance(data, dict) else data
            for ep in items or []:
                name = ep.get("name")
                if not name:
                    continue
                base_url = f"{ws}/serving-endpoints/{name}/openai/v1"
                out.append({"name": name, "provider": "databricks", "model": name, "base_url": base_url})
    except Exception:
        pass
    return out

def _get_default_gateway_route() -> Optional[dict]:
    routes = _fetch_gateway_routes()
    return routes[0] if routes else None

@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8" />
      <title>game_arena</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 2rem; }
        fieldset { margin-bottom: 1.5rem; }
        label { display:block; margin-top: .5rem; }
        textarea { width: 100%; height: 120px; }
        input, select, button { padding: .4rem; }
        .row { display:flex; gap:1rem; }
        .col { flex:1; }
        pre { background:#f7f7f7; padding:1rem; overflow:auto; }
      </style>
    </head>
    <body>
      <h1>game_arena - Databricks App</h1>
      <section>
        <h2>Text Generation</h2>
        <fieldset>
          <div class="row">
            <div class="col">
              <label>Provider</label>
              <select id="gen_provider">
                <option value="databricks" selected>databricks</option>
                <option value="openai">openai</option>
                <option value="gemini">gemini</option>
                <option value="anthropic">anthropic</option>
                <option value="together">together</option>
                <option value="xai">xai</option>
              </select>
            </div>
            <div class="col">
              <label>Model / Endpoint</label>
              <select id="gen_model"></select>
            </div>
            <div class="col">
              <label>Max tokens</label>
              <input type="number" id="gen_max" value="1024" />
            </div>
            <div class="col">
              <label>Temperature</label>
              <input type="number" step="0.1" id="gen_temp" value="0.2" />
            </div>
          </div>
          <label>Prompt</label>
          <textarea id="gen_prompt">Write a haiku about Databricks Apps.</textarea>
          <div style="margin-top:.5rem">
            <button id="btn_generate">Generate</button>
          </div>
        </fieldset>
        <pre id="gen_output"></pre>
      </section>
      <section>
        <h2>Start Chess Game</h2>
        <fieldset>
          <div class="row">
            <div class="col">
              <label>Moves</label>
              <input type="number" id="cg_moves" value="6" />
            </div>
            <div class="col">
              <label>Log chain-of-thought</label>
              <input type="checkbox" id="cg_thoughts" />
            </div>
          </div>
          <div class="row">
            <div class="col">
              <h4>White</h4>
              <label>Provider</label>
              <select id="w_provider">
                <option value="databricks" selected>databricks</option>
                <option value="openai">openai</option>
                <option value="gemini">gemini</option>
                <option value="anthropic">anthropic</option>
                <option value="together">together</option>
                <option value="xai">xai</option>
              </select>
              <label>Model / Endpoint</label>
              <select id="w_model"></select>
              <label>Max tokens</label>
              <input type="number" id="w_max" value="1024" />
              <label>Temperature</label>
              <input type="number" step="0.1" id="w_temp" value="0.2" />
            </div>
            <div class="col">
              <h4>Black</h4>
              <label>Provider</label>
              <select id="b_provider">
                <option value="databricks" selected>databricks</option>
                <option value="openai">openai</option>
                <option value="gemini">gemini</option>
                <option value="anthropic">anthropic</option>
                <option value="together">together</option>
                <option value="xai">xai</option>
              </select>
              <label>Model / Endpoint</label>
              <select id="b_model"></select>
              <label>Max tokens</label>
              <input type="number" id="b_max" value="1024" />
              <label>Temperature</label>
              <input type="number" step="0.1" id="b_temp" value="0.2" />
            </div>
          </div>
          <div style="margin-top:.5rem">
            <button id="btn_start">Start Game</button>
          </div>
        </fieldset>
        <pre id="cg_output"></pre>
      </section>
      <script>
        async function fetchRoutes() {
          try {
            const r = await fetch('/routes');
            if (!r.ok) return [];
            const j = await r.json();
            return j.routes || [];
          } catch (e) { return []; }
        }
        function fillSelect(sel, items) {
          sel.innerHTML = '';
          if (!items.length) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '(enter model name)';
            sel.appendChild(opt);
            return;
          }
          for (const it of items) {
            const opt = document.createElement('option');
            opt.value = it.name || it.model || '';
            opt.textContent = it.name || it.model || '';
            sel.appendChild(opt);
          }
        }
        async function init() {
          const routes = await fetchRoutes();
          const genModel = document.getElementById('gen_model');
          const wModel = document.getElementById('w_model');
          const bModel = document.getElementById('b_model');
          fillSelect(genModel, routes);
          fillSelect(wModel, routes);
          fillSelect(bModel, routes);
        }
        document.getElementById('btn_generate').addEventListener('click', async () => {
          const body = {
            provider: document.getElementById('gen_provider').value,
            model_name: document.getElementById('gen_model').value,
            prompt: document.getElementById('gen_prompt').value,
            temperature: parseFloat(document.getElementById('gen_temp').value),
            max_tokens: parseInt(document.getElementById('gen_max').value, 10),
            stream: false,
            timeout: 600
          };
          const r = await fetch('/generate', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
          const out = document.getElementById('gen_output');
          out.textContent = r.ok ? JSON.stringify(await r.json(), null, 2) : (await r.text());
        });
        document.getElementById('btn_start').addEventListener('click', async () => {
          const body = {
            num_moves: parseInt(document.getElementById('cg_moves').value, 10),
            log_thoughts: document.getElementById('cg_thoughts').checked,
            player_white: {
              provider: document.getElementById('w_provider').value,
              model_name: document.getElementById('w_model').value,
              temperature: parseFloat(document.getElementById('w_temp').value),
              max_tokens: parseInt(document.getElementById('w_max').value, 10),
            },
            player_black: {
              provider: document.getElementById('b_provider').value,
              model_name: document.getElementById('b_model').value,
              temperature: parseFloat(document.getElementById('b_temp').value),
              max_tokens: parseInt(document.getElementById('b_max').value, 10),
            }
          };
          const r = await fetch('/game/chess/start', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
          const out = document.getElementById('cg_output');
          out.textContent = r.ok ? JSON.stringify(await r.json(), null, 2) : (await r.text());
        });
        init();
      </script>
    </body>
    </html>
    """

@app.get("/routes")
def routes():
    return {"routes": _fetch_gateway_routes()}