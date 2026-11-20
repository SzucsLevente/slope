/*
 * libslope-keepalive.so -- macOS-style app lifetime for GTK apps on Slope.
 *
 * On a Mac, closing an app's last window really closes the window (unsaved
 * changes are asked about, the window is destroyed), but the app keeps
 * running: it stays in the Dock, and opening it again gives a fresh window.
 *
 * Most Linux apps quit as soon as their last window is gone.  For apps built
 * on GtkApplication (GTK 3 and GTK 4: GNOME apps, most Xfce apps...) this
 * library, preloaded into the session with LD_PRELOAD, holds the application
 * once it has started, so it keeps running with no windows.  Opening the app
 * again (Dock, Launchpad, Spotlight) starts it the normal way; GLib forwards
 * that to the already running copy, which opens a new window.
 *
 * Older GTK apps that run gtk_main() themselves (xfce4-terminal, galculator,
 * xfce4-taskmanager...) quit by calling gtk_main_quit() when their last window
 * is gone.  For those, that call is ignored while no window is left open, so
 * they keep running too.  xfce4-terminal opens new windows in its running
 * copy; the others are marked unique=0, so opening them again starts a fresh
 * copy and the old one is asked to quit.
 *
 * Each held app announces itself in $XDG_RUNTIME_DIR/slope-apps/<pid> so the
 * Dock can show it as running.  To really quit it, Slope creates
 * <pid>.quit there and closes its windows; once the app has no windows left
 * the hold is released and the app exits normally.
 *
 * It only acts on GtkApplications with an application id that are not
 * launchers.  Apps that allow several copies of themselves (NON_UNIQUE, like
 * GNOME Calculator) are marked unique=0: opening one of those again starts
 * a fresh copy, and Slope ends the old copy that has no windows left.  Every other program --
 * and any process that never calls g_application_run() -- is left alone.
 * The library does not link against GLib; GLib/GTK functions are looked up
 * at run time, inside processes that already use them.
 * Only apps opened *as apps* are kept running: started from a .desktop file
 * (Dock, Launchpad, Spotlight, the file manager: GLib then sets
 * GIO_LAUNCHED_DESKTOP_FILE_PID to our pid) or D-Bus activated by one.  A
 * GTK tool run from a script or a terminal (zenity, file-roller
 * --extract-here...) keeps its normal behaviour and exits when done.
 * Set SLOPE_NO_KEEPALIVE=1 to switch it off for one app.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef void *gpointer;
typedef int gint;
typedef int gboolean;
typedef unsigned int guint;
typedef unsigned long GType;
typedef struct _GList { gpointer data; struct _GList *next, *prev; } GList;

#define G_APPLICATION_IS_SERVICE  (1 << 0)
#define G_APPLICATION_IS_LAUNCHER (1 << 1)
#define G_APPLICATION_NON_UNIQUE  (1 << 5)

static gint (*real_run)(gpointer, gint, char **);
static gpointer the_app;
static int held, unique = 1;
static char marker[600], quitflag[600];
static time_t quit_seen;

#define FN(ret, name, args) ret (*name##_p) args = (ret (*) args) dlsym(RTLD_DEFAULT, #name)

static int is_gtk_application(gpointer obj)
{
    FN(GType, g_type_from_name, (const char *));
    FN(gboolean, g_type_check_instance_is_a, (gpointer, GType));
    if (!g_type_from_name_p || !g_type_check_instance_is_a_p)
        return 0;
    GType t = g_type_from_name_p("GtkApplication");
    return t && g_type_check_instance_is_a_p(obj, t);
}

static void remove_marker(void)
{
    if (marker[0])
        unlink(marker);
    if (quitflag[0])
        unlink(quitflag);
}

static void release(void)
{
    FN(void, g_application_release, (gpointer));
    if (held && g_application_release_p) {
        held = 0;
        g_application_release_p(the_app);
    }
}

/* every half second: has Slope asked this app to quit? */
static int no_open_windows(void);
static int main_mode, main_quitting;
static void (*real_main_quit)(void);

static gboolean tick(gpointer data)
{
    FN(GList *, gtk_application_get_windows, (gpointer));
    (void) data;
    if (main_mode) {                    /* a gtk_main() app */
        if (access(quitflag, F_OK) == 0 && no_open_windows()) {
            unlink(quitflag);
            main_quitting = 1;
            if (real_main_quit)
                real_main_quit();
            return 0;
        }
        return 1;
    }
    if (!held)
        return 0;
    if (access(quitflag, F_OK) != 0) {
        quit_seen = 0;
        return 1;
    }
    if (!quit_seen)
        quit_seen = time(NULL);
    if (!gtk_application_get_windows_p || !gtk_application_get_windows_p(the_app)) {
        unlink(quitflag);
        release();                      /* no windows left: quit now */
        return 0;
    }
    if (time(NULL) - quit_seen > 20)    /* a window stayed open (the user  */
        unlink(quitflag);               /* cancelled "save changes?")      */
    return 1;
}

static const char *runtime_dir(char *dir, size_t len)
{
    const char *rt = getenv("XDG_RUNTIME_DIR");
    if (rt && *rt)
        snprintf(dir, len, "%s/slope-apps", rt);
    else
        snprintf(dir, len, "/tmp/slope-apps-%u", (unsigned) getuid());
    if (mkdir(dir, 0700) != 0 && errno != EEXIST)
        return NULL;
    return dir;
}

/* started as an app: from a .desktop file, for this very process */
static int launched_as_app(void)
{
    const char *lpid = getenv("GIO_LAUNCHED_DESKTOP_FILE_PID");
    return lpid && atoi(lpid) == (int) getpid() && !getenv("SLOPE_NO_KEEPALIVE");
}

static void write_marker(const char *dir, const char *id, const char *path)
{
    char exe[512] = "";
    ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
    FILE *f;
    exe[n > 0 ? n : 0] = 0;
    snprintf(marker, sizeof marker, "%s/%d", dir, (int) getpid());
    snprintf(quitflag, sizeof quitflag, "%s/%d.quit", dir, (int) getpid());
    unlink(quitflag);
    f = fopen(marker, "w");
    if (f) {
        const char *desktop = launched_as_app() ? getenv("GIO_LAUNCHED_DESKTOP_FILE") : NULL;
        fprintf(f, "id=%s\n", id ? id : "");
        fprintf(f, "path=%s\n", path ? path : "");
        fprintf(f, "desktop=%s\n", desktop ? desktop : "");
        fprintf(f, "exe=%s\n", exe);
        fprintf(f, "unique=%d\n", unique);
        fclose(f);
    }
    atexit(remove_marker);
}

static void on_startup(gpointer app, gpointer data)
{
    FN(void, g_application_hold, (gpointer));
    FN(const char *, g_application_get_application_id, (gpointer));
    FN(const char *, g_application_get_dbus_object_path, (gpointer));
    FN(guint, g_timeout_add, (guint, gboolean (*)(gpointer), gpointer));
    char dir[400];
    (void) data;
    FN(guint, g_application_get_flags, (gpointer));
    int as_app = launched_as_app() ||
        (g_application_get_flags_p && (g_application_get_flags_p(app) & G_APPLICATION_IS_SERVICE));

    if (held || !as_app || !g_application_hold_p || !g_timeout_add_p)
        return;
    if (!runtime_dir(dir, sizeof dir))
        return;

    the_app = app;
    g_application_hold_p(app);
    held = 1;
    write_marker(dir, g_application_get_application_id_p(app),
                 g_application_get_dbus_object_path_p ? g_application_get_dbus_object_path_p(app) : NULL);
    g_timeout_add_p(500, tick, NULL);
}

gint g_application_run(gpointer app, gint argc, char **argv)
{
    if (!real_run)
        real_run = (gint (*)(gpointer, gint, char **)) dlsym(RTLD_NEXT, "g_application_run");
    if (!real_run)
        return 1;

    if (app && !getenv("SLOPE_NO_KEEPALIVE") && is_gtk_application(app)) {
        FN(guint, g_application_get_flags, (gpointer));
        FN(const char *, g_application_get_application_id, (gpointer));
        FN(unsigned long, g_signal_connect_data, (gpointer, const char *,
           void (*)(gpointer, gpointer), gpointer, gpointer, int));
        if (getenv("SLOPE_KEEPALIVE_DEBUG") && g_application_get_flags_p && g_application_get_application_id_p)
            fprintf(stderr, "slope-keepalive: %s flags=0x%x\n",
                    g_application_get_application_id_p(app) ?: "(no id)", g_application_get_flags_p(app));
        if (g_application_get_flags_p && g_application_get_application_id_p &&
            g_signal_connect_data_p && g_application_get_application_id_p(app) &&
            !(g_application_get_flags_p(app) & G_APPLICATION_IS_LAUNCHER)) {
            unique = !(g_application_get_flags_p(app) & G_APPLICATION_NON_UNIQUE);
            /* "startup" only runs in the primary instance, never in a copy
               that just forwards its command line to an already running one */
            g_signal_connect_data_p(app, "startup", on_startup, NULL, NULL, 1 /* AFTER */);
        }
    }
    gint r = real_run(app, argc, argv);
    remove_marker();
    return r;
}


/* ---- apps that run gtk_main() themselves -------------------------------- */

static int no_open_windows(void)
{
    FN(GList *, gtk_window_list_toplevels, (void));
    FN(gboolean, gtk_widget_get_visible, (gpointer));
    FN(void, g_list_free, (GList *));
    GList *l, *all;
    int open = 0;
    if (!gtk_window_list_toplevels_p || !gtk_widget_get_visible_p)
        return 0;
    all = gtk_window_list_toplevels_p();
    for (l = all; l; l = l->next)
        if (gtk_widget_get_visible_p(l->data))
            open = 1;
    if (g_list_free_p)
        g_list_free_p(all);
    return !open;
}

void gtk_main(void)
{
    static void (*real_main)(void);
    FN(guint, gtk_main_level, (void));
    FN(guint, g_timeout_add, (guint, gboolean (*)(gpointer), gpointer));
    char dir[400];

    if (!real_main)
        real_main = (void (*)(void)) dlsym(RTLD_NEXT, "gtk_main");
    if (!real_main)
        return;
    if (!main_mode && !held && launched_as_app() && gtk_main_level_p &&
        gtk_main_level_p() == 0 && g_timeout_add_p && runtime_dir(dir, sizeof dir)) {
        char exe[512] = "";
        const char *base;
        ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
        exe[n > 0 ? n : 0] = 0;
        base = strrchr(exe, '/') ? strrchr(exe, '/') + 1 : exe;
        /* xfce4-terminal opens new windows in its running copy */
        unique = !strcmp(base, "xfce4-terminal");
        main_mode = 1;
        write_marker(dir, base, NULL);
        g_timeout_add_p(500, tick, NULL);
    }
    real_main();
}

void gtk_main_quit(void)
{
    FN(guint, gtk_main_level, (void));
    if (!real_main_quit)
        real_main_quit = (void (*)(void)) dlsym(RTLD_NEXT, "gtk_main_quit");
    if (!real_main_quit)
        return;
    /* the outermost loop is told to quit while no window is open: that is
       "the last window was closed" -- keep running, Mac-style */
    if (main_mode && !main_quitting && gtk_main_level_p && gtk_main_level_p() == 1 &&
        no_open_windows() && access(quitflag, F_OK) != 0)
        return;
    real_main_quit();
}
