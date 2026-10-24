//! The monkey itself: behaviour, physics and interaction state machine.

pub const GRAVITY: f32 = 2600.0;
pub const WALK_SPEED: f32 = 46.0;
pub const NAP_AFTER: f32 = 90.0;
pub const IDLE_DOZE: f32 = 120.0;
pub const SHOWER_SECS: f32 = 7.0;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    Idle,
    Walk,
    Happy,
    Grab,
    Fall,
    Land,
    Sleep,
    Eat,
    Doze,
    Run,
    Shower,
}

impl State {
    pub fn anim(self) -> &'static str {
        match self {
            State::Idle => "idle",
            State::Walk => "walk",
            State::Happy => "happy",
            State::Grab => "grab",
            State::Fall => "fall",
            State::Land => "land",
            State::Sleep => "sleep",
            State::Eat => "eat",
            State::Doze => "sleep",
            State::Run => "walk",
            State::Shower => "shower",
        }
    }
}

/// Usable desktop area, in window-top-left coordinates.
#[derive(Clone, Copy)]
pub struct Bounds {
    pub left: f32,
    pub right: f32,
    pub top: f32,
    pub floor: f32,
}

struct Rng(u32);

impl Rng {
    fn next(&mut self) -> u32 {
        // xorshift32: plenty for picking behaviours
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.0 = x;
        x
    }
    fn f32(&mut self) -> f32 {
        (self.next() >> 8) as f32 / 16_777_216.0
    }
    fn range(&mut self, a: f32, b: f32) -> f32 {
        a + (b - a) * self.f32()
    }
}

pub struct Pet {
    pub state: State,
    pub anim_t: f32,
    pub x: f32,
    pub y: f32,
    pub vx: f32,
    pub vy: f32,
    pub facing: i8,
    pub dragging: bool,
    quiet: f32,
    wander: f32,
    walk_for: f32,
    rng: Rng,
}

impl Pet {
    pub fn new(x: f32, y: f32) -> Pet {
        Pet {
            state: State::Idle,
            anim_t: 0.0,
            x,
            y,
            vx: 0.0,
            vy: 0.0,
            facing: 1,
            dragging: false,
            quiet: 0.0,
            wander: 6.0,
            walk_for: 0.0,
            rng: Rng(0x9E37_79B9),
        }
    }

    pub fn set_state(&mut self, s: State) {
        if self.state != s {
            self.state = s;
            self.anim_t = 0.0;
        }
    }

    pub fn wake(&mut self) {
        self.quiet = 0.0;
        if self.state == State::Sleep || self.state == State::Doze {
            self.set_state(State::Idle);
        }
    }

    // --- interaction ------------------------------------------------------
    pub fn grab(&mut self) {
        self.wake();
        self.dragging = true;
        self.vx = 0.0;
        self.vy = 0.0;
        self.set_state(State::Grab);
    }

    pub fn release(&mut self, vx: f32, vy: f32) {
        self.dragging = false;
        self.quiet = 0.0;
        let speed = (vx * vx + vy * vy).sqrt();
        if speed > 240.0 {
            self.vx = vx.clamp(-2600.0, 2600.0);
            self.vy = vy.clamp(-2600.0, 2600.0);
            self.set_state(State::Fall);
        } else {
            self.set_state(State::Land);
        }
    }

    pub fn pat(&mut self) {
        self.wake();
        self.set_state(State::Happy);
    }

    pub fn feed(&mut self) {
        self.wake();
        self.set_state(State::Eat);
    }

    pub fn play(&mut self) {
        self.wake();
        self.set_state(State::Happy);
    }

    pub fn nap(&mut self) {
        self.quiet = 0.0;
        self.set_state(State::Sleep);
    }

    pub fn jump(&mut self) {
        self.wake();
        self.set_state(State::Happy);
    }

    pub fn shower(&mut self) {
        self.wake();
        self.set_state(State::Shower);
    }

    // --- per-frame --------------------------------------------------------
    pub fn tick(&mut self, dt: f32, b: &Bounds, user_inactive: bool) {
        let dt = dt.min(0.1);
        self.anim_t += dt;
        self.quiet += dt;

        if self.dragging {
            return;
        }

        match self.state {
            State::Fall => {
                self.vy += GRAVITY * dt;
                self.x += self.vx * dt;
                self.y += self.vy * dt;
                self.vx *= 0.995;

                if self.x < b.left {
                    self.x = b.left;
                    self.vx = -self.vx * 0.55;
                } else if self.x > b.right {
                    self.x = b.right;
                    self.vx = -self.vx * 0.55;
                }
                if self.y < b.top {
                    self.y = b.top;
                    self.vy = 0.0;
                }
                if self.y >= b.floor {
                    self.y = b.floor;
                    if self.vy > 760.0 {
                        self.vy = -self.vy * 0.34;
                    } else {
                        self.vx = 0.0;
                        self.vy = 0.0;
                        self.set_state(State::Land);
                    }
                }
            }
            State::Walk => {
                self.x += self.facing as f32 * WALK_SPEED * dt;
                if self.x <= b.left {
                    self.x = b.left;
                    self.facing = 1;
                } else if self.x >= b.right {
                    self.x = b.right;
                    self.facing = -1;
                }
                self.walk_for -= dt;
                if self.walk_for <= 0.0 {
                    self.set_state(State::Idle);
                    self.wander = self.rng.range(4.0, 13.0);
                }
            }
            State::Run => {
                let spd = WALK_SPEED * 2.6;
                self.x += self.facing as f32 * spd * dt;
                if self.x <= b.left {
                    self.x = b.left;
                    self.facing = 1;
                } else if self.x >= b.right {
                    self.x = b.right;
                    self.facing = -1;
                }
                self.walk_for -= dt;
                if self.walk_for <= 0.0 {
                    self.set_state(State::Idle);
                    self.wander = self.rng.range(3.0, 10.0);
                }
            }
            State::Doze => {
                // Curl up and doze; wait for activity/user
                if !user_inactive {
                    self.quiet = 0.0;
                    self.set_state(State::Idle);
                }
                if self.rng.f32() < 0.01 {
                    self.facing = if self.rng.f32() < 0.5 { -1 } else { 1 };
                }
            }
            State::Idle => {
                self.wander -= dt;
                if self.wander <= 0.0 {
                    self.decide(b);
                }
                if user_inactive {
                    if self.quiet > IDLE_DOZE {
                        self.set_state(State::Doze);
                    } else if self.quiet > NAP_AFTER {
                        self.set_state(State::Sleep);
                    }
                } else if self.quiet > NAP_AFTER {
                    self.set_state(State::Sleep);
                }
            }
            State::Sleep => {
                if !user_inactive {
                    self.quiet = 0.0;
                    self.set_state(State::Idle);
                }
            }
            State::Shower => {
                if self.anim_t >= SHOWER_SECS {
                    self.set_state(State::Idle);
                }
            }
            _ => {}
        }
    }

    fn decide(&mut self, b: &Bounds) {
        let r = self.rng.f32();
        self.wander = self.rng.range(4.0, 12.0);
        if r < 0.25 {
            // run all around randomly
            let room_left = self.x - b.left;
            let room_right = b.right - self.x;
            if room_left < 80.0 {
                self.facing = 1;
            } else if room_right < 80.0 {
                self.facing = -1;
            } else {
                self.facing = if self.rng.f32() < 0.5 { -1 } else { 1 };
            }
            self.walk_for = self.rng.range(2.2, 5.4);
            self.set_state(State::Run);
        } else if r < 0.65 {
            // amble somewhere
            let room_left = self.x - b.left;
            let room_right = b.right - self.x;
            if room_left < 40.0 {
                self.facing = 1;
            } else if room_right < 40.0 {
                self.facing = -1;
            } else {
                self.facing = if self.rng.f32() < 0.5 { -1 } else { 1 };
            }
            self.walk_for = self.rng.range(1.1, 3.2);
            self.set_state(State::Walk);
        } else if r < 0.8 {
            self.set_state(State::Happy);
        }
    }

    /// Snap back inside the desktop (e.g. after a monitor change).
    pub fn clamp(&mut self, b: &Bounds) {
        self.x = self.x.clamp(b.left, b.right);
        self.y = self.y.clamp(b.top, b.floor);
    }
}
