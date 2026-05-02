package com.btone.c.events;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;

import java.io.OutputStream;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Server-Sent Events endpoint. One subscription per HTTP request,
 * blocks the request thread.
 *
 * Events from the EventBus arrive on arbitrary threads (Render thread
 * for chat, Baritone thread for path, etc.). The subscriber enqueues
 * events into a thread-safe queue; this handler's thread is the ONLY
 * writer to the HTTP output stream, eliminating the concurrent-write
 * race that was silently dropping chat events.
 */
public final class SseEndpoint {
    private final EventBus bus;
    private final ObjectMapper mapper = new ObjectMapper();

    public SseEndpoint(EventBus bus) { this.bus = bus; }

    public void handle(HttpExchange ex) {
        try {
            ex.getResponseHeaders().set("Content-Type", "text/event-stream");
            ex.getResponseHeaders().set("Cache-Control", "no-cache");
            ex.sendResponseHeaders(200, 0);
        } catch (Exception e) {
            return;
        }
        OutputStream out = ex.getResponseBody();

        // Thread-safe queue: subscriber (any thread) enqueues; this thread dequeues.
        var queue = new LinkedBlockingQueue<EventBus.Event>();
        var sub = bus.subscribe(queue::add);

        try {
            while (true) {
                // Block up to 5 seconds waiting for the next event.
                EventBus.Event ev = queue.poll(5, TimeUnit.SECONDS);
                if (ev == null) {
                    // No events in 5 seconds — send keepalive to detect dead clients.
                    out.write(": keepalive\n\n".getBytes());
                    out.flush();
                } else {
                    // Write the event, then drain any queued events for throughput.
                    writeEvent(out, ev);
                    EventBus.Event next;
                    while ((next = queue.poll()) != null) {
                        writeEvent(out, next);
                    }
                    out.flush();
                }
            }
        } catch (Throwable ignored) {
            // Client disconnected or stream error — clean up.
        } finally {
            try { sub.close(); } catch (Throwable ignored) {}
            try { out.close(); } catch (Throwable ignored) {}
        }
    }

    private void writeEvent(OutputStream out, EventBus.Event ev) throws Exception {
        String line = "event: " + ev.type() + "\n"
                + "data: " + mapper.writeValueAsString(ev) + "\n\n";
        out.write(line.getBytes());
    }
}
