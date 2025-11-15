# Minimal FastAPI app to run on Databricks Apps for game_arena
from __future__ import annotations
from typing import Literal, Optional, Any, Dict
import os
import uuid
import urllib.parse
from contextlib import nullcontext as _nullcontext
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio
import json
from game_arena.harness import tournament_util
from game_arena.harness import model_generation_sdk
from game_arena.harness import model_generation_http
from game_arena.harness import databricks_sdk
from game_arena.harness import game_notation_examples, prompt_generation, prompts, parsers
import pyspiel
import mlflow
from app.chess_logging import ChessRecorder
from app import db_sql
import requests
# Optional MLflow GenAI Tracing (no-op if not available)
# Removed advanced client API (start_trace/start_span) and feature gates.
# We will use the high-level decorator API: @mlflow.trace
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
        return databricks_sdk.DatabricksModel(
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
# --- High-level tracing helpers using decorator API ---
@mlflow.trace(name="llm_generate", span_type="llm")
def _llm_generate(model: Any, prompt_text: str, player: Optional[str] = None) -> Dict[str, Any]:
    mi = tournament_util.ModelTextInput(prompt_text=prompt_text)
    ret = model.generate_with_text_input(mi)
    out: Dict[str, Any] = {
        "text": ret.main_response,
        "usage": {
            "prompt_tokens": ret.prompt_tokens,
            "generation_tokens": ret.generation_tokens,
            "reasoning_tokens": getattr(ret, "reasoning_tokens", None),
        },
        "request_for_logging": getattr(ret, "request_for_logging", None),
        "response_for_logging": getattr(ret, "response_for_logging", None),
        "player": player,
    }
    # Always capture chain-of-thought if provided by the model
    if hasattr(ret, "main_response_and_thoughts"):
        out["chain_of_thought"] = ret.main_response_and_thoughts
    return out
@mlflow.trace(name="app_generate", span_type="chain")
def _trace_generate(req: 'GenerateRequest') -> Dict[str, Any]:
    model = _build_model(req)
    llm_out = _llm_generate(model, req.prompt, player=None)
    return {
        "text": llm_out["text"],
        "usage": llm_out["usage"],
        "provider": req.provider,
        "model_name": req.model_name,
    }
@mlflow.trace(name="generate_prompt", span_type="chain")
def _generate_chess_prompt(state: Any) -> Dict[str, Any]:
    prompt_gen = prompt_generation.PromptGeneratorText()
    prompt_template = prompts.PromptTemplate.NO_LEGAL_ACTIONS
    subs = {
        "readable_state_str": tournament_util.convert_to_readable_state(
            game_short_name="chess",
            state_str=state.to_string(),
            current_player=state.current_player(),
        ),
        "move_history": (tournament_util.get_action_string_history(state) or "None"),
        "player_name": game_notation_examples.GAME_SPECIFIC_NOTATIONS["chess"]["player_map"][state.current_player()],
        "move_notation": game_notation_examples.GAME_SPECIFIC_NOTATIONS["chess"]["move_notation"],
        "notation": game_notation_examples.GAME_SPECIFIC_NOTATIONS["chess"]["state_notation"],
    }
    prompt = prompt_gen.generate_prompt_with_text_only(
        prompt_template=prompt_template, game_short_name="chess", **subs
    )
    return {"prompt_text": prompt.prompt_text}
@mlflow.trace(name="parse_move", span_type="tool")
def _parse_move_span(text_to_parse: str, state: Any) -> Dict[str, Any]:
    parser = parsers.ChainedMoveParser([parsers.RuleBasedMoveParser(), parsers.SoftMoveParser("chess")])
    pin = parsers.TextParserInput(
        text=text_to_parse,
        state_str=state.to_string(),
        legal_moves=parsers.get_legal_action_strings(state),
        player_number=state.current_player(),
    )
    move = parser.parse(pin)
    return {"parsed_move": move, "is_valid": bool(move)}
@mlflow.trace(name="chess_game", span_type="chain")
def _run_chess_game(num_moves: int, model_white: Any, model_black: Any, run_id: Optional[str]) -> Dict[str, Any]:
    game = pyspiel.load_game("chess")
    state = game.new_initial_state()
    rec = ChessRecorder(headers={"Event": "game_arena", "Site": "Databricks App", "Run": str(run_id) if run_id else ""}, write_files=False)
    for move_number in range(num_moves):
        if state.is_terminal():
            break
        # Generate prompt
        gp = _generate_chess_prompt(state)
        prompt_text = gp["prompt_text"]
        # Pick model
        model = model_white if state.current_player() == 0 else model_black
        player = "white" if state.current_player() == 0 else "black"
        # Model call
        llm_out = _llm_generate(model, prompt_text, player=player)
        # Parse
        parsed = _parse_move_span(llm_out["text"], state)
        move = parsed.get("parsed_move")
        if not move:
            break
        # Apply
        rec.push_san(move)
        state.apply_action(state.string_to_action(move))
    pgn_game = tournament_util.get_pgn(state)
    pgn_str = str(pgn_game)
    result = pgn_game.headers.get("Result", "*")
    out_paths = rec.finalize(result_str=result)
    return {"pgn": pgn_str, "result": result, "artifacts": out_paths}
@app.get("/health")
def health():
    return {"status": "ok"}
@app.post("/generate")
def generate(req: GenerateRequest):
    # ...existing code...
    try:
        return _trace_generate(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
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
@app.post("/game/chess/start")
def start_chess_game(req: ChessGameRequest):
    # Ensure SQL table exists (no-op if connector not installed)
    try:
        db_sql.init_table()
    except Exception:
        pass
    mlflow.openai.autolog()
    
    # Unique experiment per game + run
    game_id = str(uuid.uuid4())
    exp_name = f"game_arena"
    mlflow.set_tracking_uri("databricks")
    
    # Dynamically determine the experiment path under the current user's folder
    try:
        # This requires the databricks-sdk to be installed and auth to be configured.
        from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]
        w = WorkspaceClient()
        username = w.current_user.me().user_name
        experiment_name = f"/Users/{username}/{exp_name}"
    except Exception as e:
        # Fallback to a shared directory if user cannot be determined.
        print(f"Could not determine username, falling back to shared experiment path. Error: {e}")
        experiment_name = f"/Shared/game_arena_games/{exp_name}"
    mlflow.set_experiment(experiment_name)
    # Ensure experiment is public (readable by all users)
    try:
        exp = mlflow.get_experiment_by_name(experiment_name)
        if exp and exp.experiment_id:
            _set_experiment_public(exp.experiment_id)
    except Exception:
        pass
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
        # Set up models for both players
        model_white = _build_model_from_player_config(req.player_white, route_w)
        model_black = _build_model_from_player_config(req.player_black, route_b)
        # Execute traced chess game loop
        game_out = _run_chess_game(req.num_moves, model_white, model_black, run_id)
        # Attach to MLflow run
        try:
            mlflow.log_text(game_out.get("pgn", ""), "game.pgn")
        except Exception:
            pass
        # Insert to SQL table if connector available
        try:
            experiment = mlflow.get_experiment_by_name(experiment_name)
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
                result=game_out.get("result"),
                pgn=game_out.get("pgn"),
            )
        except Exception:
            pass
        return {
            "experiment_name": experiment_name,
            "experiment_id": str(experiment_id) if experiment_id else None,
            "run_id": run_id,
            "result": game_out.get("result"),
            "pgn": game_out.get("pgn"),
            "artifacts": game_out.get("artifacts"),
        }
    finally:
        try:
            mlflow.end_run()
        except Exception:
            pass
def _build_model_from_player_config(pc: PlayerConfig, default_route: Optional[dict]):
    """Helper to build a model from a PlayerConfig object."""
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
def _set_experiment_public(experiment_id: str) -> None:
    """Grant All Users read access to the MLflow experiment on Databricks.
    Best-effort, no-op outside Databricks or on failure.
    """
    ws = _workspace_url()
    if not ws or not experiment_id:
        return
    headers = _auth_headers() | {"Content-Type": "application/json"}
    url = f"{ws}/api/2.0/permissions/experiments/{experiment_id}"
    payload = {
        "access_control_list": [
            {"group_name": "users", "permission_level": "CAN_READ"}
        ]
    }
    try:
        r = requests.patch(url, headers=headers, json=payload, timeout=10)
        if not r.ok:
            # Fallback to PUT (replace) if PATCH not supported
            requests.put(url, headers=headers, json=payload, timeout=10)
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
            // Attach metadata so we can route correctly per-selection
            if (it.base_url) opt.dataset.baseUrl = it.base_url;
            if (it.provider) opt.dataset.provider = it.provider;
            sel.appendChild(opt);
          }
        }
        async function init() {
          const routes = await fetchRoutes();
          // Keep for lookup if needed later
          window.__routes = routes;
          const genModel = document.getElementById('gen_model');
          const wModel = document.getElementById('w_model');
          const bModel = document.getElementById('b_model');
          fillSelect(genModel, routes);
          fillSelect(wModel, routes);
          fillSelect(bModel, routes);
        }
        document.getElementById('btn_generate').addEventListener('click', async () => {
          const provider = document.getElementById('gen_provider').value;
          const sel = document.getElementById('gen_model');
          const selectedOpt = sel.options[sel.selectedIndex];
          const baseUrl = selectedOpt && selectedOpt.dataset ? selectedOpt.dataset.baseUrl : undefined;
          const body = {
            provider,
            model_name: sel.value,
            prompt: document.getElementById('gen_prompt').value,
            temperature: parseFloat(document.getElementById('gen_temp').value),
            max_tokens: parseInt(document.getElementById('gen_max').value, 10),
            stream: false,
            timeout: 600
          };
          // For non-Databricks providers, use the selected route base_url to go through Gateway/OpenAI
          if (provider !== 'databricks' && baseUrl) {
            body.base_url = baseUrl;
          }
          const r = await fetch('/generate', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
          const out = document.getElementById('gen_output');
          out.textContent = r.ok ? JSON.stringify(await r.json(), null, 2) : (await r.text());
        });
        document.getElementById('btn_start').addEventListener('click', async () => {
          const wProv = document.getElementById('w_provider').value;
          const wSel = document.getElementById('w_model');
          const wOpt = wSel.options[wSel.selectedIndex];
          const wBase = wOpt && wOpt.dataset ? wOpt.dataset.baseUrl : undefined;
          const bProv = document.getElementById('b_provider').value;
          const bSel = document.getElementById('b_model');
          const bOpt = bSel.options[bSel.selectedIndex];
          const bBase = bOpt && bOpt.dataset ? bOpt.dataset.baseUrl : undefined;
          const body = {
            num_moves: parseInt(document.getElementById('cg_moves').value, 10),
            player_white: {
              provider: wProv,
              model_name: wSel.value,
              temperature: parseFloat(document.getElementById('w_temp').value),
              max_tokens: parseInt(document.getElementById('w_max').value, 10),
            },
            player_black: {
              provider: bProv,
              model_name: bSel.value,
              temperature: parseFloat(document.getElementById('b_temp').value),
              max_tokens: parseInt(document.getElementById('b_max').value, 10),
            }
          };
          // Include base_url when not using Databricks provider so backend routes to the selected endpoint
          if (wProv !== 'databricks' && wBase) body.player_white.base_url = wBase;
          if (bProv !== 'databricks' && bBase) body.player_black.base_url = bBase;
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

# ============================================================================
# Chess UI Integration - Player vs LLM
# ============================================================================

# Mount the React frontend
app.mount("/chess-ui", StaticFiles(directory="frontend/dist", html=True), name="chess-ui")

class MakeMoveRequest(BaseModel):
    fen: str  # Current board position
    move: str  # Player's move in SAN notation
    provider: Literal["databricks", "openai", "gemini", "anthropic", "together", "xai"] = "databricks"
    model_name: str = "auto"
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 1024

# Store active games in memory (in production, use Redis or database)
active_games: Dict[str, Any] = {}

@app.post("/chess/make-move")
async def make_move(req: MakeMoveRequest):
    """
    Player makes a move, LLM responds with its move and chain of thought.
    Returns the LLM's move and thoughts immediately (non-streaming).
    """
    try:
        # Create a pyspiel state from FEN
        game = pyspiel.load_game("chess")
        state = game.deserialize_state(req.fen)

        # Apply player's move
        try:
            action = state.string_to_action(req.move)
            state.apply_action(action)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid move: {str(e)}")

        # Check if game is over
        if state.is_terminal():
            return {
                "game_over": True,
                "result": tournament_util.get_pgn(state).headers.get("Result", "*"),
                "fen": state.to_string(),
            }

        # Build model for LLM
        model_req = GenerateRequest(
            provider=req.provider,
            model_name=req.model_name,
            prompt="",  # Will be generated
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=False,
        )
        model = _build_model(model_req)

        # Generate prompt for current position
        gp = _generate_chess_prompt(state)
        prompt_text = gp["prompt_text"]

        # Get LLM move with chain of thought
        llm_out = _llm_generate(model, prompt_text, player="black")

        # Parse the move
        parsed = _parse_move_span(llm_out["text"], state)
        llm_move = parsed.get("parsed_move")

        if not llm_move:
            raise HTTPException(status_code=500, detail="LLM failed to generate valid move")

        # Apply LLM's move
        action = state.string_to_action(llm_move)
        state.apply_action(action)

        # Check if game is now over
        game_over = state.is_terminal()
        result = None
        if game_over:
            result = tournament_util.get_pgn(state).headers.get("Result", "*")

        return {
            "llm_move": llm_move,
            "chain_of_thought": llm_out.get("chain_of_thought", llm_out.get("text")),
            "fen": state.to_string(),
            "game_over": game_over,
            "result": result,
            "usage": llm_out.get("usage"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chess/stream-move")
async def stream_move(req: MakeMoveRequest):
    """
    Player makes a move, LLM responds with streaming chain of thought.
    Returns Server-Sent Events stream.
    """
    async def event_generator():
        try:
            # Create a pyspiel state from FEN
            game = pyspiel.load_game("chess")
            state = game.deserialize_state(req.fen)

            # Apply player's move
            try:
                action = state.string_to_action(req.move)
                state.apply_action(action)
                yield f"data: {json.dumps({'type': 'player_move', 'move': req.move, 'fen': state.to_string()})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Invalid move: {str(e)}'})}\n\n"
                return

            # Check if game is over
            if state.is_terminal():
                result = tournament_util.get_pgn(state).headers.get("Result", "*")
                yield f"data: {json.dumps({'type': 'game_over', 'result': result, 'fen': state.to_string()})}\n\n"
                return

            # Stream thinking notification
            yield f"data: {json.dumps({'type': 'thinking', 'message': 'LLM is thinking...'})}\n\n"

            # Build model for LLM with streaming enabled
            model_req = GenerateRequest(
                provider=req.provider,
                model_name=req.model_name,
                prompt="",
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                stream=True,  # Enable streaming
            )
            model = _build_model(model_req)

            # Generate prompt for current position
            gp = _generate_chess_prompt(state)
            prompt_text = gp["prompt_text"]

            # Stream prompt
            yield f"data: {json.dumps({'type': 'prompt', 'text': prompt_text})}\n\n"

            # Get LLM move with chain of thought
            # Note: Current implementation aggregates stream, but we can yield chunks
            llm_out = _llm_generate(model, prompt_text, player="black")

            # Stream chain of thought in chunks
            cot = llm_out.get("chain_of_thought", llm_out.get("text", ""))
            # Split into sentences for progressive display
            sentences = cot.split('. ')
            for i, sentence in enumerate(sentences):
                if sentence.strip():
                    yield f"data: {json.dumps({'type': 'thought_chunk', 'text': sentence + ('. ' if i < len(sentences) - 1 else '')})}\n\n"
                    await asyncio.sleep(0.1)  # Small delay for readability

            # Parse the move
            parsed = _parse_move_span(llm_out["text"], state)
            llm_move = parsed.get("parsed_move")

            if not llm_move:
                yield f"data: {json.dumps({'type': 'error', 'message': 'LLM failed to generate valid move'})}\n\n"
                return

            # Stream the move
            yield f"data: {json.dumps({'type': 'llm_move', 'move': llm_move})}\n\n"

            # Apply LLM's move
            action = state.string_to_action(llm_move)
            state.apply_action(action)

            # Check if game is now over
            if state.is_terminal():
                result = tournament_util.get_pgn(state).headers.get("Result", "*")
                yield f"data: {json.dumps({'type': 'game_over', 'result': result, 'fen': state.to_string()})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'move_complete', 'fen': state.to_string()})}\n\n"

            # Final usage stats
            yield f"data: {json.dumps({'type': 'usage', 'usage': llm_out.get('usage')})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ============================================================================
# LLM vs LLM Arena - Streaming Game
# ============================================================================

class ArenaGameRequest(BaseModel):
    num_moves: int = 20
    player_white: PlayerConfig
    player_black: PlayerConfig
    log_thoughts: Optional[bool] = True

@app.post("/game/chess/start_streaming")
async def start_streaming_arena_game(req: ArenaGameRequest):
    """
    Start an LLM vs LLM chess game with Server-Sent Events streaming.
    Streams move-by-move updates with chain of thought for each player.
    """
    async def event_generator():
        try:
            # Generate unique game ID
            game_id = str(uuid.uuid4())

            # Send game started event
            yield f"data: {json.dumps({'type': 'game_started', 'game_id': game_id})}\n\n"

            # Initialize chess game
            game = pyspiel.load_game("chess")
            state = game.new_initial_state()

            # Build models for both players
            routes = _fetch_gateway_routes()
            route_w = routes[0] if len(routes) > 0 else None
            route_b = routes[1] if len(routes) > 1 else route_w

            model_white = _build_model_from_player_config(req.player_white, route_w)
            model_black = _build_model_from_player_config(req.player_black, route_b)

            # Game loop
            move_number = 0
            for i in range(req.num_moves):
                if state.is_terminal():
                    break

                move_number += 1
                current_player = state.current_player()
                # PySpiel chess: player 0 is Black, player 1 is White
                player_name = "black" if current_player == 0 else "white"
                model = model_black if current_player == 0 else model_white

                # Send thinking notification
                yield f"data: {json.dumps({'type': 'thinking', 'player': player_name, 'move_number': move_number})}\n\n"

                # Generate prompt for current position
                gp = _generate_chess_prompt(state)
                prompt_text = gp["prompt_text"]

                # Get LLM response with chain of thought
                llm_out = _llm_generate(model, prompt_text, player=player_name)

                # Parse the move
                parsed = _parse_move_span(llm_out["text"], state)
                move_san = parsed.get("parsed_move")

                if not move_san:
                    # LLM failed to generate valid move - game ends
                    yield f"data: {json.dumps({'type': 'error', 'message': f'{player_name} failed to generate valid move', 'player': player_name})}\n\n"
                    break

                # Apply the move
                try:
                    action = state.string_to_action(move_san)
                    state.apply_action(action)
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to apply move {move_san}: {str(e)}', 'player': player_name})}\n\n"
                    break

                # Get reasoning tokens if available
                reasoning_tokens = llm_out.get("usage", {}).get("reasoning_tokens")

                # Send move event with full details
                move_event = {
                    'type': 'move',
                    'move_number': move_number,
                    'player': player_name,
                    'move_san': move_san,
                    'fen': state.to_string(),
                    'thoughts': llm_out.get("chain_of_thought", llm_out.get("text", "")) if req.log_thoughts else "",
                    'reasoning_tokens': reasoning_tokens,
                    'is_terminal': state.is_terminal()
                }
                yield f"data: {json.dumps(move_event)}\n\n"

                # Small delay to allow client to process
                await asyncio.sleep(0.05)

                # Check if game ended
                if state.is_terminal():
                    break

            # Game complete - send final event
            pgn_game = tournament_util.get_pgn(state)
            pgn_str = str(pgn_game)
            result = pgn_game.headers.get("Result", "*")

            complete_event = {
                'type': 'game_complete',
                'game_id': game_id,
                'result': result,
                'pgn': pgn_str,
                'total_moves': move_number
            }
            yield f"data: {json.dumps(complete_event)}\n\n"

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            print(f"Arena game error: {error_detail}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")