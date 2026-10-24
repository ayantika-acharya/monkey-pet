//! Monkey Pet - a pixel-art monkey that lives on your desktop.
//!
//! A single layered Win32 window (per-pixel alpha, always on top, never steals
//! focus) with a CPU-composited sprite. No GPU context, no framework, no
//! background threads: the whole thing idles at a few megabytes.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod atlas;
mod log;
mod pet;

use std::cell::Cell;
use std::ffi::c_void;
use std::ptr::{null, null_mut};

use windows_sys::Win32::Foundation::*;
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::SystemInformation::GetTickCount64;
use windows_sys::Win32::System::Registry::*;
use windows_sys::Win32::UI::HiDpi::*;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, ReleaseCapture, SetCapture};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use pet::{Pet, State};

const TIMER_ID: usize = 1;
const CLASS_NAME: &[u16] = &[
    'M' as u16, 'o' as u16, 'n' as u16, 'k' as u16, 'e' as u16, 'y' as u16,
    'P' as u16, 'e' as u16, 't' as u16, 0,
];

// menu commands
const CMD_FEED: usize = 1;
const CMD_NAP: usize = 2;
const CMD_PLAY: usize = 3;
const CMD_HOME: usize = 4;
const CMD_QUIT: usize = 5;
const CMD_SHOWER: usize = 6;

struct App {
    hwnd: HWND,
    atlas: atlas::Atlas,
    pet: Pet,
    scale: i32,
    win_w: i32,
    win_h: i32,
    mem_dc: HDC,
    screen_dc: HDC,
    _dib: HBITMAP,
    bits: *mut u8,
    frame: u32,
    drawn: (u32, i8),
    last_x: i32,
    last_y: i32,
    dragging: bool,
    drag_off: (i32, i32),
    drag_from: (i32, i32),
    last_mouse: (i32, i32),
    last_mouse_t: u64,
    drag_dist: f32,
    press_t: u64,
    vel: (f32, f32),
    timer_ms: u32,
    last_state: State,
    ticks: u32,
    b_down: bool,
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

impl App {
    unsafe fn new(hwnd: HWND) -> App {
        let atlas = atlas::Atlas::load().expect("sprite atlas is corrupt; re-run tools/spritegen.py");

        let dpi = GetDpiForSystem().max(96) as f32;
        let scale = (4.0 * dpi / 96.0).round().clamp(2.0, 8.0) as i32;
        let win_w = atlas.w as i32 * scale;
        let win_h = atlas.h as i32 * scale;

        let screen_dc = GetDC(null_mut());
        let mem_dc = CreateCompatibleDC(screen_dc);
        let mut bmi: BITMAPINFO = std::mem::zeroed();
        bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bmi.bmiHeader.biWidth = win_w;
        bmi.bmiHeader.biHeight = -win_h; // top-down
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB;
        let mut bits: *mut c_void = null_mut();
        let dib = CreateDIBSection(mem_dc, &bmi, DIB_RGB_COLORS, &mut bits, null_mut(), 0);
        SelectObject(mem_dc, dib as HGDIOBJ);

        let mut app = App {
            hwnd,
            atlas,
            pet: Pet::new(0.0, 0.0),
            scale,
            win_w,
            win_h,
            mem_dc,
            screen_dc,
            _dib: dib,
            bits: bits as *mut u8,
            frame: 0,
            drawn: (u32::MAX, 0),
            last_x: i32::MIN,
            last_y: i32::MIN,
            dragging: false,
            drag_off: (0, 0),
            drag_from: (0, 0),
            last_mouse: (0, 0),
            last_mouse_t: 0,
            drag_dist: 0.0,
            press_t: 0,
            vel: (0.0, 0.0),
            timer_ms: 0,
            last_state: State::Idle,
            ticks: 0,
            b_down: false,
        };

        // Start on the floor near the bottom-right corner.
        let b = app.bounds();
        app.pet.x = b.right - 80.0;
        app.pet.y = b.floor;
        app.pet.clamp(&b);

        log::log(&format!("window {}x{} scale {}", win_w, win_h, scale));
        app.last_x = app.pet.x.round() as i32;
        app.last_y = app.pet.y.round() as i32;
        app.sync_position();
        app.drawn = (u32::MAX, 0);
        app.tick(0.0);
        app.set_timer(33);
        app
    }

    unsafe fn bounds(&self) -> pet::Bounds {
        let mon = MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST);
        let mut mi: MONITORINFO = std::mem::zeroed();
        mi.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
        if GetMonitorInfoW(mon, &mut mi) != 0 {
            let r = mi.rcWork;
            pet::Bounds {
                left: r.left as f32,
                right: (r.right - self.win_w) as f32,
                top: r.top as f32,
                floor: (r.bottom - self.win_h) as f32,
            }
        } else {
            pet::Bounds { left: 0.0, right: 1920.0, top: 0.0, floor: 1040.0 }
        }
    }

    /// Compose the current sprite frame into the DIB and hand it to the window.
    unsafe fn draw(&mut self) {
        let (fw, fh, s) = (self.atlas.w as i32, self.atlas.h as i32, self.scale);
        let src = self.atlas.frame(self.frame);
        let dw = self.win_w;
        let stride = (dw * 4) as usize;
        let dst = std::slice::from_raw_parts_mut(self.bits, stride * self.win_h as usize);

        for y in 0..fh {
            for x in 0..fw {
                let sx = if self.pet.facing < 0 { fw - 1 - x } else { x };
                let p = ((y * fw + sx) * 4) as usize;
                let px = [src[p], src[p + 1], src[p + 2], src[p + 3]];
                for dy in 0..s {
                    let row = ((y * s + dy) as usize) * stride;
                    for dx in 0..s {
                        let o = row + ((x * s + dx) * 4) as usize;
                        dst[o..o + 4].copy_from_slice(&px);
                    }
                }
            }
        }
        self.present();
    }

    unsafe fn present(&mut self) {
        let pt_dst = POINT { x: self.last_x.max(0), y: self.last_y.max(0) };
        let size = SIZE { cx: self.win_w, cy: self.win_h };
        let pt_src = POINT { x: 0, y: 0 };
        let blend = BLENDFUNCTION {
            BlendOp: AC_SRC_OVER as u8,
            BlendFlags: 0,
            SourceConstantAlpha: 255,
            AlphaFormat: AC_SRC_ALPHA as u8,
        };
        UpdateLayeredWindow(
            self.hwnd,
            self.screen_dc,
            &pt_dst,
            &size,
            self.mem_dc,
            &pt_src,
            0,
            &blend,
            ULW_ALPHA,
        );
    }

    unsafe fn set_timer(&mut self, ms: u32) {
        if self.timer_ms != ms {
            self.timer_ms = ms;
            SetTimer(self.hwnd, TIMER_ID, ms, None);
        }
    }

    unsafe fn sync_position(&mut self) {
        let nx = self.pet.x.round() as i32;
        let ny = self.pet.y.round() as i32;
        if nx != self.last_x || ny != self.last_y {
            self.last_x = nx;
            self.last_y = ny;
            SetWindowPos(
                self.hwnd,
                HWND_TOPMOST,
                nx,
                ny,
                0,
                0,
                SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
            );
        }
    }

    unsafe fn tick(&mut self, dt: f32) {
        let b = self.bounds();
        #[repr(C)]
        struct LASTINPUTINFO {
            cb_size: u32,
            dw_time: u32,
        }
        unsafe extern "system" {
            fn GetLastInputInfo(plii: *mut LASTINPUTINFO) -> i32;
        }
        let mut lii = LASTINPUTINFO {
            cb_size: std::mem::size_of::<LASTINPUTINFO>() as u32,
            dw_time: 0,
        };
        let inactive_ms = if unsafe { GetLastInputInfo(&mut lii) } != 0 {
            let now = unsafe { GetTickCount64() } as u32;
            if now >= lii.dw_time { now - lii.dw_time } else { 0 }
        } else {
            0
        };
        let user_inactive = inactive_ms > 120_000;
        self.pet.tick(dt, &b, user_inactive);

        // B is polled rather than handled in WM_KEYDOWN: the pet window is
        // WS_EX_NOACTIVATE, so it never owns keyboard focus. This keeps the
        // shortcut working while another app is in front.
        let b_down = (GetAsyncKeyState('B' as i32) as u16 & 0x8000) != 0;
        if b_down && !self.b_down {
            self.pet.shower();
        }
        self.b_down = b_down;

        self.ticks = self.ticks.wrapping_add(1);
        if self.pet.state != self.last_state {
            log::log(&format!(
                "state {:?} -> {:?} at ({:.0},{:.0})",
                self.last_state, self.pet.state, self.pet.x, self.pet.y
            ));
            self.last_state = self.pet.state;
        }
        if self.ticks % 120 == 0 {
            log::log(&format!("alive tick {} state {:?}", self.ticks, self.pet.state));
        }

        // Put the sprite's feet back on the floor after a drag, and keep the
        // pet on the current monitor.
        self.pet.clamp(&b);

        // End of a one-shot animation -> back to idling.
        let (fps, n, looping) = {
            let a = self.atlas.anim(self.pet.state.anim());
            (a.fps.max(1), a.frames.len() as u32, a.looping)
        };
        let dur = n as f32 / fps as f32;
        if !looping && self.pet.anim_t >= dur {
            self.pet.set_state(State::Idle);
        }

        let a = self.atlas.anim(self.pet.state.anim());
        let idx = (self.pet.anim_t * a.fps as f32) as usize;
        let idx = if a.looping { idx % a.frames.len() } else { idx.min(a.frames.len() - 1) };
        self.frame = a.frames[idx];

        self.sync_position();
        if self.drawn != (self.frame, self.pet.facing) {
            self.drawn = (self.frame, self.pet.facing);
            self.draw();
        }

        let want = if self.pet.dragging || self.pet.state == State::Fall {
            16
        } else if self.pet.state == State::Sleep {
            120
        } else {
            33
        };
        self.set_timer(want);
    }

    unsafe fn menu(&mut self) {
        let menu = CreatePopupMenu();
        let feed = wide("Feed a banana\tF");
        let nap = wide("Take a nap");
        let play = wide("Play");
        let home = wide("Come here");
        let shower = wide("Take a shower\tB");
        let quit = wide("Quit");
        AppendMenuW(menu, MF_STRING, CMD_FEED, feed.as_ptr());
        AppendMenuW(menu, MF_STRING, CMD_NAP, nap.as_ptr());
        AppendMenuW(menu, MF_STRING, CMD_PLAY, play.as_ptr());
        AppendMenuW(menu, MF_STRING, CMD_SHOWER, shower.as_ptr());
        AppendMenuW(menu, MF_SEPARATOR, 0, null());
        AppendMenuW(menu, MF_STRING, CMD_HOME, home.as_ptr());
        AppendMenuW(menu, MF_STRING, CMD_QUIT, quit.as_ptr());

        let mut pt = POINT { x: 0, y: 0 };
        GetCursorPos(&mut pt);
        SetForegroundWindow(self.hwnd);
        let cmd = TrackPopupMenu(
            menu,
            TPM_RETURNCMD | TPM_RIGHTBUTTON,
            pt.x,
            pt.y,
            0,
            self.hwnd,
            null(),
        ) as usize;
        PostMessageW(self.hwnd, WM_NULL, 0, 0);
        DestroyMenu(menu);

        match cmd {
            CMD_FEED => self.pet.feed(),
            CMD_NAP => self.pet.nap(),
            CMD_PLAY => self.pet.play(),
            CMD_SHOWER => self.pet.shower(),
            CMD_HOME => {
                let b = self.bounds();
                self.pet.x = b.right;
                self.pet.y = b.floor;
                self.pet.set_state(State::Land);
            }
            CMD_QUIT => {
                DestroyWindow(self.hwnd);
            }
            _ => {}
        }
    }

    unsafe fn on_message(&mut self, msg: u32, wp: WPARAM, lp: LPARAM) -> Option<LRESULT> {
        match msg {
            WM_TIMER => {
                // The timer interval we asked for is our timestep.
                self.tick(self.timer_ms as f32 / 1000.0);
                Some(0)
            }
            WM_NCHITTEST => {
                let mut pt = POINT {
                    x: (lp & 0xFFFF) as i16 as i32,
                    y: ((lp >> 16) & 0xFFFF) as i16 as i32,
                };
                ScreenToClient(self.hwnd, &mut pt);
                let cx = pt.x / self.scale;
                let cy = pt.y / self.scale;
                if cx < 0 || cy < 0 || cx >= self.atlas.w as i32 || cy >= self.atlas.h as i32 {
                    return Some(HTTRANSPARENT as LRESULT);
                }
                let a = self.atlas.alpha(self.frame, cx as u32, cy as u32, self.pet.facing);
                // Transparent pixels let clicks through to whatever is underneath.
                if a < 24 {
                    Some(HTTRANSPARENT as LRESULT)
                } else {
                    Some(HTCLIENT as LRESULT)
                }
            }
            WM_LBUTTONDOWN => {
                // Wake on single click if dozing or sleeping
                if self.pet.state == State::Doze || self.pet.state == State::Sleep {
                    self.pet.wake();
                    self.pet.pat();
                    return Some(0);
                }
                let mut pt = POINT { x: 0, y: 0 };
                GetCursorPos(&mut pt);
                self.drag_off = (pt.x - self.last_x, pt.y - self.last_y);
                self.drag_from = (self.last_x, self.last_y);
                self.last_mouse = (self.last_x, self.last_y);
                self.last_mouse_t = GetTickCount64();
                self.press_t = self.last_mouse_t;
                self.drag_dist = 0.0;
                self.vel = (0.0, 0.0);
                self.dragging = true;
                self.pet.grab();
                SetCapture(self.hwnd);
                Some(0)
            }
            WM_MOUSEMOVE => {
                if self.dragging {
                    let mut pt = POINT { x: 0, y: 0 };
                    GetCursorPos(&mut pt);
                    let nx = pt.x - self.drag_off.0;
                    let ny = pt.y - self.drag_off.1;
                    let now = GetTickCount64();
                    let dt = ((now - self.last_mouse_t) as f32 / 1000.0).max(0.001);
                    let vx = (nx - self.last_mouse.0) as f32 / dt;
                    let vy = (ny - self.last_mouse.1) as f32 / dt;
                    self.vel.0 = self.vel.0 * 0.55 + vx * 0.45;
                    self.vel.1 = self.vel.1 * 0.55 + vy * 0.45;
                    self.last_mouse = (nx, ny);
                    self.last_mouse_t = now;
                    self.drag_dist += (((nx - self.last_x) as f32).powi(2)
                        + ((ny - self.last_y) as f32).powi(2))
                    .sqrt();

                    let b = self.bounds();
                    self.pet.x = (nx as f32).clamp(b.left, b.right);
                    self.pet.y = (ny as f32).clamp(b.top, b.floor);
                    self.sync_position();
                    self.pet.set_state(State::Grab);
                }
                Some(0)
            }
            WM_LBUTTONDBLCLK => {
                self.dragging = false;
                ReleaseCapture();
                self.pet.jump();
                Some(0)
            }
            WM_LBUTTONUP => {
                if self.dragging {
                    ReleaseCapture();
                    self.dragging = false;
                    let quick = GetTickCount64().saturating_sub(self.press_t) < 450;
                    if self.drag_dist < 5.0 && quick {
                        self.pet.pat(); // a tap: the monkey is pleased
                    } else {
                        let (vx, vy) = self.vel;
                        self.pet.release(vx, vy);
                    }
                }
                Some(0)
            }
            WM_CAPTURECHANGED => {
                if self.dragging {
                    // capture lost (alt-tab, etc): drop the monkey
                    self.dragging = false;
                    self.pet.dragging = false;
                    let b = self.bounds();
                    self.pet.y = self.pet.y.min(b.floor);
                    self.pet.set_state(State::Fall);
                }
                Some(0)
            }
            WM_RBUTTONUP => {
                self.menu();
                Some(0)
            }
            WM_KEYDOWN => {
                if wp == 'F' as usize {
                    self.pet.feed();
                } else if wp == 'Q' as usize {
                    DestroyWindow(self.hwnd);
                } else if wp == 'B' as usize {
                    self.pet.shower();
                }
                Some(0)
            }
            WM_ERASEBKGND => Some(1),
            WM_DESTROY => {
                PostQuitMessage(0);
                Some(0)
            }
            WM_CLOSE => {
                DestroyWindow(self.hwnd);
                Some(0)
            }
            _ => None,
        }
    }
}

thread_local! {
    static APP_PTR: Cell<*mut App> = const { Cell::new(null_mut()) };
    static IN_WNDPROC: Cell<bool> = const { Cell::new(false) };
}

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    // Never re-enter while processing a message (prevents TrackPopupMenu/pump reentrancy)
    if IN_WNDPROC.get() {
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
    IN_WNDPROC.set(true);
    let res = APP_PTR.with(|p| {
        let ptr = p.get();
        if ptr.is_null() {
            None
        } else {
            match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                (*ptr).on_message(msg, wp, lp)
            })) {
                Ok(r) => r,
                Err(e) => {
                    let s = if let Some(x) = e.downcast_ref::<&str>() {
                        *x
                    } else if let Some(x) = e.downcast_ref::<String>() {
                        x.as_str()
                    } else {
                        "?"
                    };
                    log::log(&format!("panic on_message 0x{:04X}: {}", msg, s));
                    Some(0)
                }
            }
        }
    });
    IN_WNDPROC.set(false);
    match res {
        Some(r) => r,
        None => DefWindowProcW(hwnd, msg, wp, lp),
    }
}

fn main() {
    log::init();
    std::panic::set_hook(Box::new(|info| {
        let loc = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_default();
        let msg = if let Some(s) = info.payload().downcast_ref::<&str>() {
            (*s).to_string()
        } else if let Some(s) = info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "unknown panic".to_string()
        };
        log::log(&format!("PANIC at {}: {}", loc, msg));
        log::log(&format!(
            "backtrace:\n{}",
            std::backtrace::Backtrace::force_capture()
        ));
    }));
    log::log("startup");

    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

        let hinstance = GetModuleHandleW(null()) as HINSTANCE;
        let cursor = LoadCursorW(null_mut(), IDC_ARROW);

        let mut wc: WNDCLASSEXW = std::mem::zeroed();
        wc.cbSize = std::mem::size_of::<WNDCLASSEXW>() as u32;
        wc.style = CS_HREDRAW | CS_VREDRAW | CS_DBLCLKS;
        wc.lpfnWndProc = Some(wndproc);
        wc.hInstance = hinstance;
        wc.hCursor = cursor;
        wc.lpszClassName = CLASS_NAME.as_ptr();
        if RegisterClassExW(&wc) == 0 {
            return;
        }

        let ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE;
        let hwnd = CreateWindowExW(
            ex,
            CLASS_NAME.as_ptr(),
            wide("Monkey").as_ptr(),
            WS_POPUP,
            200,
            200,
            160,
            160,
            null_mut(),
            null_mut(),
            hinstance,
            null(),
        );
        if hwnd.is_null() {
            return;
        }

        let app = Box::into_raw(Box::new(App::new(hwnd)));
        APP_PTR.with(|c| c.set(app));

        ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        UpdateWindow(hwnd);

        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, null_mut(), 0, 0) > 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        unsafe { let _ = Box::from_raw(app); }
    }
}
