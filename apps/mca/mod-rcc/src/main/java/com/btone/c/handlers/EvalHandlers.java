package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcHandler;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.minecraft.client.Minecraft;
import org.luaj.vm2.Globals;
import org.luaj.vm2.LuaError;
import org.luaj.vm2.LuaTable;
import org.luaj.vm2.LuaValue;
import org.luaj.vm2.Varargs;
import org.luaj.vm2.lib.TwoArgFunction;
import org.luaj.vm2.lib.jse.CoerceJavaToLua;
import org.luaj.vm2.lib.jse.JsePlatform;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * {@code debug.eval} — the lowest-level, zero-codegen escape hatch. Runs an
 * arbitrary Lua snippet <b>on the client thread</b> with helpers pre-bound in
 * scope, returns the script's value JSON-serialised plus captured stdout.
 *
 * <h2>Engine: Luaj (org.luaj:luaj-jse 3.0.1)</h2>
 * Chosen over the other JDK-17 options:
 * <ul>
 *   <li><b>Standalone Nashorn</b> — Nashorn was removed from the JDK in 15, so
 *       {@code getEngineByName("nashorn")} returns null on stock 17. The
 *       standalone {@code org.openjdk.nashorn:nashorn-core} artifact works but
 *       is larger (~2.5 MB + ASM) and JS is off-theme for a CC/Lua project.</li>
 *   <li><b>GraalVM JS</b> — tens of MB, needs the Graal SDK; overkill here.</li>
 *   <li><b>Luaj</b> — one ~350 KB pure-Java jar, no transitive deps, runs on
 *       stock JDK 17 with no {@code ScriptEngineManager}, and Lua matches this
 *       project's ComputerCraft/Lua heritage. Chosen.</li>
 * </ul>
 *
 * <h2>The name problem at runtime (the crux)</h2>
 * Luaj's luajava bridge lets a script call Java methods reflectively, resolving
 * each method <b>by literal name at call time</b>. That is exactly where
 * intermediary remapping bites:
 * <ul>
 *   <li>The pre-bound {@code mc} / {@code player} / {@code world} are the mod's
 *       own already-resolved references, so <b>reaching</b> them costs nothing.</li>
 *   <li>But a call like {@code player:getHealth()} makes Luaj look up a method
 *       literally named {@code getHealth} on {@code ClientPlayerEntity} at
 *       runtime. In a <b>production</b> (deployed) Fabric client MC runs under
 *       intermediary names — {@code getHealth} only exists as {@code method_6032}
 *       — so the reflective lookup fails with "method_6032 is not a member".
 *       Readable calls on raw MC objects therefore work only in the <b>Loom dev
 *       workspace</b>, not against the shipped jar.</li>
 * </ul>
 * <b>Workaround (and the reason this approach is production-viable):</b> the
 * curated {@link ScriptApi} object is bound as the global {@code api}. Its
 * methods are compiled into <i>this mod's jar</i>; Loom remaps the yarn calls in
 * their bodies at build time, while the method names on {@code ScriptApi} itself
 * ({@code api:health()}, {@code api:blockAt(x,y,z)}, …) are our own identifiers
 * and are never remapped. So <b>scripts written against {@code api} run
 * identically in dev and production</b>; raw {@code mc}/{@code player}/{@code
 * world} are still bound for dev-time exploration and documented as dev-only for
 * reflective method calls.
 */
public final class EvalHandlers {
    private static final ObjectMapper M = new ObjectMapper();
    private static final long DEFAULT_TIMEOUT_MS = 3_000;
    private static final long MAX_TIMEOUT_MS = 15_000;

    // How many Lua VM instructions between deadline checks. Small enough to
    // interrupt a tight loop promptly, large enough not to dominate runtime.
    private static final int HOOK_EVERY_INSTRUCTIONS = 2_000;

    private EvalHandlers() {}

    public static void registerAll(RpcRouter r) {
        r.register("debug.eval", eval());
    }

    private static RpcHandler eval() {
        return params -> {
            String code = params.path("code").asText(null);
            if (code == null || code.isBlank()) {
                throw new IllegalArgumentException("missing 'code'");
            }
            long timeoutMs = params.has("timeout_ms")
                    ? Math.max(1, Math.min(params.get("timeout_ms").asLong(), MAX_TIMEOUT_MS))
                    : DEFAULT_TIMEOUT_MS;

            // Run the script ON the client thread. We give the outer wait a
            // little slack over the in-VM deadline so the instruction hook is
            // what actually stops a runaway loop (and reports a clean error),
            // not the HTTP-side timeout.
            long outerWaitMs = Math.min(timeoutMs + 2_000, MAX_TIMEOUT_MS + 2_000);
            return ClientThread.call(outerWaitMs, () -> runScript(code, timeoutMs));
        };
    }

    /** Executes on the client thread. */
    private static ObjectNode runScript(String code, long timeoutMs) {
        Minecraft mc = Minecraft.getInstance();

        // debugGlobals() installs the debug library, which we need for the
        // instruction-count hook that enforces the timeout.
        Globals g = JsePlatform.debugGlobals();

        // Capture stdout (print / io.write) into a buffer.
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        g.STDOUT = new PrintStream(out, true, StandardCharsets.UTF_8);

        // Bind helpers.
        g.set("mc", CoerceJavaToLua.coerce(mc));
        g.set("player", CoerceJavaToLua.coerce(mc.player));
        g.set("world", CoerceJavaToLua.coerce(mc.level));
        g.set("api", CoerceJavaToLua.coerce(new ScriptApi(mc)));

        // Timeout enforcement: a count hook throws a LuaError once wall-clock
        // exceeds the deadline. Because eval runs single-threaded on the client
        // thread it cannot be pre-empted from outside; the in-VM hook is the
        // only reliable way to stop a runaway loop without wedging the client.
        final long deadlineNanos = System.nanoTime() + timeoutMs * 1_000_000L;
        LuaValue hook = new TwoArgFunction() {
            @Override
            public LuaValue call(LuaValue event, LuaValue line) {
                if (System.nanoTime() > deadlineNanos) {
                    throw new LuaError("eval timeout after " + timeoutMs + "ms");
                }
                return NIL;
            }
        };
        // debug.sethook(hook, mask, count): count>0 → hook every `count` instructions.
        g.get("debug").get("sethook")
                .invoke(LuaValue.varargsOf(new LuaValue[]{
                        hook, LuaValue.valueOf(""), LuaValue.valueOf(HOOK_EVERY_INSTRUCTIONS)}));

        ObjectNode resp = M.createObjectNode();
        try {
            LuaValue chunk = g.load(code, "eval");
            Varargs rv = chunk.invoke();
            resp.put("ok", true);
            if (rv.narg() <= 1) {
                resp.set("result", luaToJson(rv.arg1(), 0));
            } else {
                ArrayNode arr = M.createArrayNode();
                for (int i = 1; i <= rv.narg(); i++) arr.add(luaToJson(rv.arg(i), 0));
                resp.set("result", arr);
            }
        } catch (LuaError le) {
            resp.put("ok", false);
            resp.put("error", String.valueOf(le.getMessage()));
        } finally {
            g.STDOUT.flush();
            String captured = out.toString(StandardCharsets.UTF_8);
            if (!captured.isEmpty()) resp.put("stdout", captured);
        }
        return resp;
    }

    // ===== Lua → JSON =====

    private static final int MAX_DEPTH = 8;

    private static JsonNode luaToJson(LuaValue v, int depth) {
        if (v == null) return M.nullNode();
        switch (v.type()) {
            case LuaValue.TNIL:
                return M.nullNode();
            case LuaValue.TBOOLEAN:
                return M.getNodeFactory().booleanNode(v.toboolean());
            case LuaValue.TNUMBER:
                return v.isint()
                        ? M.getNodeFactory().numberNode(v.toint())
                        : M.getNodeFactory().numberNode(v.todouble());
            case LuaValue.TSTRING:
                return M.getNodeFactory().textNode(v.tojstring());
            case LuaValue.TTABLE:
                if (depth >= MAX_DEPTH) return M.getNodeFactory().textNode(v.tojstring());
                return tableToJson(v.checktable(), depth);
            default:
                // function / userdata (coerced Java object) / thread — best-effort string.
                return M.getNodeFactory().textNode(v.tojstring());
        }
    }

    private static JsonNode tableToJson(LuaTable t, int depth) {
        List<LuaValue> keys = new ArrayList<>();
        List<LuaValue> vals = new ArrayList<>();
        LuaValue k = LuaValue.NIL;
        while (true) {
            Varargs n = t.next(k);
            k = n.arg1();
            if (k.isnil()) break;
            keys.add(k);
            vals.add(n.arg(2));
        }
        // Treat as a JSON array iff keys are exactly the integers 1..n in order.
        boolean isArray = !keys.isEmpty();
        for (int i = 0; i < keys.size(); i++) {
            LuaValue key = keys.get(i);
            if (!key.isint() || key.toint() != i + 1) { isArray = false; break; }
        }
        if (isArray) {
            ArrayNode a = M.createArrayNode();
            for (LuaValue vv : vals) a.add(luaToJson(vv, depth + 1));
            return a;
        }
        ObjectNode o = M.createObjectNode();
        for (int i = 0; i < keys.size(); i++) {
            o.set(keys.get(i).tojstring(), luaToJson(vals.get(i), depth + 1));
        }
        return o;
    }
}
