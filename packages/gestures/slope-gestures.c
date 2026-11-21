/*
 * slope-gestures -- trackpad gestures for the Slope desktop.
 *
 * Listens to the X server's own touchpad gestures (XInput 2.4, delivered by
 * the libinput driver) and runs a command for each one, as set in
 * ~/.config/slope/gestures.conf (written by System Settings -> Trackpad):
 *
 *     swipe-up-3   = slope-launchpad
 *     swipe-left-3 = slope-ctl key alt+Right
 *     pinch-in-4   = slope-launchpad
 *
 * Gesture names: swipe-{up,down,left,right}-{3,4} and pinch-{in,out}-{2,3,4}.
 * Two-finger scrolling is not a gesture (it is scrolling) and is left alone.
 * The file is re-read whenever it changes.  Three- and four-finger swipes are
 * grabbed so they work over every window; pinches are only seen when the
 * window under the pointer does not use them itself (browsers zoom with them).
 */
#define _DEFAULT_SOURCE
#include <X11/Xlib.h>
#include <X11/extensions/XInput2.h>
#include <ctype.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAXRULES 64

static char conf_path[1024];
static struct timespec conf_mtime;
static off_t conf_size = -1;
static struct { char name[32]; char cmd[512]; } rules[MAXRULES];
static int nrules;
static int verbose;

static char *trim(char *s)
{
    char *e;
    while (isspace((unsigned char) *s))
        s++;
    e = s + strlen(s);
    while (e > s && isspace((unsigned char) e[-1]))
        *--e = 0;
    return s;
}

static void load_conf(void)
{
    struct stat st;
    FILE *f;
    char line[1024];

    if (stat(conf_path, &st) != 0) {
        nrules = 0;
        conf_size = -1;
        return;
    }
    if (st.st_mtim.tv_sec == conf_mtime.tv_sec && st.st_mtim.tv_nsec == conf_mtime.tv_nsec &&
        st.st_size == conf_size)
        return;
    conf_mtime = st.st_mtim;
    conf_size = st.st_size;
    nrules = 0;
    f = fopen(conf_path, "r");
    if (!f)
        return;
    while (fgets(line, sizeof line, f) && nrules < MAXRULES) {
        char *eq, *k, *v;
        if (line[0] == '#' || !(eq = strchr(line, '=')))
            continue;
        *eq = 0;
        k = trim(line);
        v = trim(eq + 1);
        if (!*k || !*v)
            continue;
        snprintf(rules[nrules].name, sizeof rules[nrules].name, "%s", k);
        snprintf(rules[nrules].cmd, sizeof rules[nrules].cmd, "%s", v);
        nrules++;
    }
    fclose(f);
}

static void run(const char *gesture)
{
    load_conf();
    if (verbose)
        fprintf(stderr, "slope-gestures: %s\n", gesture);
    for (int i = 0; i < nrules; i++) {
        if (strcmp(rules[i].name, gesture) != 0)
            continue;
        if (fork() == 0) {
            setsid();
            execl("/bin/sh", "sh", "-c", rules[i].cmd, (char *) NULL);
            _exit(127);
        }
        return;
    }
}

/* never exit on an X error (e.g. another copy already grabbed the swipes) */
static int ignore_x_errors(Display *d, XErrorEvent *e)
{
    (void) d;
    if (verbose)
        fprintf(stderr, "slope-gestures: X error %d (request %d)\n", e->error_code, e->request_code);
    return 0;
}

int main(int argc, char **argv)
{
    Display *dpy;
    Window root;
    int opcode, evb, errb, major = 2, minor = 4;
    unsigned char bits[XIMaskLen(XI_LASTEVENT)] = { 0 };
    XIEventMask mask = { XIAllMasterDevices, sizeof bits, bits };
    XIGrabModifiers mods = { XIAnyModifier, 0 };
    double dx = 0, dy = 0, scale = 1;
    int fingers = 0;
    const char *home = getenv("HOME");

    verbose = argc > 1 && !strcmp(argv[1], "-v");
    signal(SIGCHLD, SIG_IGN);           /* no zombies from launched commands */
    if (getenv("XDG_CONFIG_HOME"))
        snprintf(conf_path, sizeof conf_path, "%s/slope/gestures.conf", getenv("XDG_CONFIG_HOME"));
    else
        snprintf(conf_path, sizeof conf_path, "%s/.config/slope/gestures.conf", home ? home : "");

    if (!(dpy = XOpenDisplay(NULL))) {
        fprintf(stderr, "slope-gestures: cannot open display\n");
        return 1;
    }
    if (!XQueryExtension(dpy, "XInputExtension", &opcode, &evb, &errb) ||
        XIQueryVersion(dpy, &major, &minor) != Success || major * 10 + minor < 24) {
        fprintf(stderr, "slope-gestures: the X server has no touchpad gestures (XInput 2.4)\n");
        return 0;
    }
    XSetErrorHandler(ignore_x_errors);
    root = DefaultRootWindow(dpy);
    XISetMask(bits, XI_GestureSwipeBegin);
    XISetMask(bits, XI_GestureSwipeUpdate);
    XISetMask(bits, XI_GestureSwipeEnd);
    XISetMask(bits, XI_GesturePinchBegin);
    XISetMask(bits, XI_GesturePinchUpdate);
    XISetMask(bits, XI_GesturePinchEnd);
    XISelectEvents(dpy, root, &mask, 1);
    /* swipes: grab them, so they reach us over every window */
    memset(bits, 0, sizeof bits);
    XISetMask(bits, XI_GestureSwipeBegin);
    XISetMask(bits, XI_GestureSwipeUpdate);
    XISetMask(bits, XI_GestureSwipeEnd);
    XIGrabSwipeGestureBegin(dpy, XIAllMasterDevices, root, XIGrabModeAsync,
                            XIGrabModeAsync, False, &mask, 1, &mods);
    XSync(dpy, False);
    load_conf();

    for (;;) {
        XEvent ev;
        XGenericEventCookie *c = &ev.xcookie;
        char name[32];

        XNextEvent(dpy, &ev);
        if (c->type != GenericEvent || c->extension != opcode || !XGetEventData(dpy, c))
            continue;
        switch (c->evtype) {
        case XI_GestureSwipeBegin:
        case XI_GestureSwipeUpdate:
        case XI_GestureSwipeEnd: {
            XIGestureSwipeEvent *e = c->data;
            if (c->evtype == XI_GestureSwipeBegin) {
                dx = dy = 0;
                fingers = e->detail;
            }
            dx += e->delta_unaccel_x;
            dy += e->delta_unaccel_y;
            if (c->evtype == XI_GestureSwipeEnd && !(e->flags & XIGestureSwipeEventCancelled) &&
                fmax(fabs(dx), fabs(dy)) > 40) {
                const char *dir = fabs(dx) > fabs(dy) ? (dx > 0 ? "right" : "left")
                                                      : (dy > 0 ? "down" : "up");
                snprintf(name, sizeof name, "swipe-%s-%d", dir, fingers);
                run(name);
            }
            break;
        }
        case XI_GesturePinchBegin:
        case XI_GesturePinchUpdate:
        case XI_GesturePinchEnd: {
            XIGesturePinchEvent *e = c->data;
            if (c->evtype == XI_GesturePinchBegin)
                fingers = e->detail;
            scale = e->scale;
            if (c->evtype == XI_GesturePinchEnd && !(e->flags & XIGesturePinchEventCancelled) &&
                (scale < 0.8 || scale > 1.25)) {
                snprintf(name, sizeof name, "pinch-%s-%d", scale < 1 ? "in" : "out", fingers);
                run(name);
            }
            break;
        }
        }
        XFreeEventData(dpy, c);
    }
}
