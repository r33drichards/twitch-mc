package com.btone.c.meteor;

import java.lang.reflect.Method;

/**
 * Reflective bridge to Baritone for the RunAwayFromDanger module.
 *
 * Mirrors the rest of mod-c's Baritone glue: don't compile against
 * baritone classes (already modCompileOnly above; we keep the reflective
 * path to gracefully degrade if Baritone is missing at runtime).
 */
final class BaritoneFlee {
    private BaritoneFlee() {}

    /** The baritone goal that was active before the last flee, if any. */
    private static volatile Object savedGoal = null;

    /** Stop any current Baritone process and pathfind to the goal. */
    static void fleeTo(int x, int y, int z) throws Exception {
        Class<?> apiCls = Class.forName("baritone.api.BaritoneAPI");
        Object provider = apiCls.getMethod("getProvider").invoke(null);
        Object baritone = provider.getClass().getMethod("getPrimaryBaritone").invoke(provider);

        // Save the current goal before cancelling so we can resume later
        try {
            Object cgp = baritone.getClass().getMethod("getCustomGoalProcess").invoke(baritone);
            Object currentGoal = cgp.getClass().getMethod("getGoal").invoke(cgp);
            if (currentGoal != null) {
                savedGoal = currentGoal;
            }
        } catch (Throwable ignored) {
            // getGoal may not exist on all Baritone versions
        }

        Object pathing = baritone.getClass().getMethod("getPathingBehavior").invoke(baritone);
        try {
            findMethod(pathing.getClass(), "cancelEverything").invoke(pathing);
        } catch (Throwable ignored) {
            // Older baritone uses different method name; try the customGoal stop instead
        }

        Object cgp = baritone.getClass().getMethod("getCustomGoalProcess").invoke(baritone);
        Class<?> goalCls = Class.forName("baritone.api.pathing.goals.Goal");
        Class<?> goalBlockCls = Class.forName("baritone.api.pathing.goals.GoalBlock");
        Object goal = goalBlockCls.getConstructor(int.class, int.class, int.class)
            .newInstance(x, y, z);
        cgp.getClass().getMethod("setGoalAndPath", goalCls).invoke(cgp, goal);
    }

    /**
     * Resume the baritone goal that was active before the last flee.
     * Returns true if a goal was restored, false if there was nothing saved.
     */
    static boolean resumeSavedGoal() throws Exception {
        Object goal = savedGoal;
        if (goal == null) return false;
        savedGoal = null;

        Class<?> apiCls = Class.forName("baritone.api.BaritoneAPI");
        Object provider = apiCls.getMethod("getProvider").invoke(null);
        Object baritone = provider.getClass().getMethod("getPrimaryBaritone").invoke(provider);
        Object cgp = baritone.getClass().getMethod("getCustomGoalProcess").invoke(baritone);
        Class<?> goalCls = Class.forName("baritone.api.pathing.goals.Goal");
        cgp.getClass().getMethod("setGoalAndPath", goalCls).invoke(cgp, goal);
        return true;
    }

    /** Clear any saved goal without resuming it. */
    static void clearSavedGoal() {
        savedGoal = null;
    }

    private static Method findMethod(Class<?> cls, String name) throws NoSuchMethodException {
        Class<?> c = cls;
        while (c != null) {
            try {
                Method m = c.getDeclaredMethod(name);
                m.setAccessible(true);
                return m;
            } catch (NoSuchMethodException ignored) {}
            c = c.getSuperclass();
        }
        throw new NoSuchMethodException(name);
    }
}
