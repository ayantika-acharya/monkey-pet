//! Zero-copy reader for the sprite atlas baked by `tools/spritegen.py`.
//!
//! Layout (little endian):
//!   "MPA1" | u32 w | u32 h | u32 frame_count | frames[frame_count] (BGRA, premultiplied)
//!   u32 anim_count | anims[] { u32 name_len, name, u32 fps, u32 looping, u32 n, u32 frames[n] }

pub const BYTES: &[u8] = include_bytes!("../assets/monkey.atlas");

pub struct Anim {
    pub name: String,
    pub fps: u32,
    pub looping: bool,
    pub frames: Vec<u32>,
}

pub struct Atlas {
    pub w: u32,
    pub h: u32,
    frames: &'static [u8],
    pub anims: Vec<Anim>,
}

fn u32_at(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]])
}

impl Atlas {
    pub fn load() -> Option<Atlas> {
        let b = BYTES;
        if b.len() < 16 || &b[0..4] != b"MPA1" {
            return None;
        }
        let w = u32_at(b, 4);
        let h = u32_at(b, 8);
        let n = u32_at(b, 12) as usize;
        let frame_bytes = (w * h * 4) as usize;
        let start = 16;
        let end = start + frame_bytes * n;
        if b.len() < end {
            return None;
        }
        let frames = &b[start..end];

        let mut o = end;
        let mut anims = Vec::new();
        let anim_count = u32_at(b, o) as usize;
        o += 4;
        for _ in 0..anim_count {
            let name_len = u32_at(b, o) as usize;
            o += 4;
            let name = String::from_utf8_lossy(&b[o..o + name_len]).into_owned();
            o += name_len;
            let fps = u32_at(b, o);
            let looping = u32_at(b, o + 4) != 0;
            o += 8;
            let cnt = u32_at(b, o) as usize;
            o += 4;
            let mut fs = Vec::with_capacity(cnt);
            for _ in 0..cnt {
                fs.push(u32_at(b, o));
                o += 4;
            }
            anims.push(Anim { name, fps, looping, frames: fs });
        }

        Some(Atlas { w, h, frames, anims })
    }

    pub fn frame(&self, i: u32) -> &'static [u8] {
        let fsz = (self.w * self.h * 4) as usize;
        let i = i as usize;
        &self.frames[i * fsz..(i + 1) * fsz]
    }

    pub fn anim(&self, name: &str) -> &Anim {
        self.anims
            .iter()
            .find(|a| a.name == name)
            .unwrap_or_else(|| &self.anims[0])
    }

    /// Alpha of one sprite pixel, used for click-through hit testing.
    pub fn alpha(&self, frame: u32, x: u32, y: u32, facing: i8) -> u8 {
        if x >= self.w || y >= self.h {
            return 0;
        }
        let sx = if facing < 0 { self.w - 1 - x } else { x };
        let off = ((y * self.w + sx) * 4 + 3) as usize;
        self.frame(frame)[off]
    }
}
