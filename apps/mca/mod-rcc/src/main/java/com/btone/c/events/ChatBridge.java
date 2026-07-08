package com.btone.c.events;

/**
 * Static holder for the EventBus reference used by the ChatHud mixin.
 * Mixin classes cannot hold non-private static methods or be referenced
 * directly, so this separate class acts as the bridge.
 */
public final class ChatBridge {
    private static volatile EventBus bus;

    private ChatBridge() {}

    /** Called once from BtoneC during init. */
    public static void init(EventBus eventBus) {
        bus = eventBus;
    }

    /** Returns the EventBus, or null if not yet initialized. */
    public static EventBus bus() {
        return bus;
    }
}
