//! Cola de reproducción con aleatorio y repetición (misma lógica que `playqueue.py`).

use rand::seq::SliceRandom;

use crate::models::Track;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Repeat {
    Off,
    All,
    One,
}

impl Repeat {
    pub fn parse(s: &str) -> Repeat {
        match s {
            "all" => Repeat::All,
            "one" => Repeat::One,
            _ => Repeat::Off,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Repeat::Off => "off",
            Repeat::All => "all",
            Repeat::One => "one",
        }
    }
}

#[derive(Debug)]
pub struct PlayQueue {
    pub tracks: Vec<Track>,
    /// -1 = nada seleccionado.
    pub index: isize,
    pub repeat: Repeat,
    shuffle: bool,
    original: Option<Vec<Track>>,
    /// Cambia en cada modificación (para refrescar la vista).
    pub version: u64,
}

impl Default for PlayQueue {
    fn default() -> Self {
        Self::new()
    }
}

impl PlayQueue {
    pub fn new() -> Self {
        PlayQueue {
            tracks: Vec::new(),
            index: -1,
            repeat: Repeat::Off,
            shuffle: false,
            original: None,
            version: 0,
        }
    }

    // ------------------------------------------------------------- consultas
    pub fn current(&self) -> Option<&Track> {
        if self.index >= 0 {
            self.tracks.get(self.index as usize)
        } else {
            None
        }
    }

    pub fn peek_next(&self) -> Option<&Track> {
        self.next_index(true).and_then(|i| self.tracks.get(i))
    }

    pub fn len(&self) -> usize {
        self.tracks.len()
    }

    pub fn is_empty(&self) -> bool {
        self.tracks.is_empty()
    }

    fn touch(&mut self) {
        self.version += 1;
    }

    // ------------------------------------------------------------ navegación
    fn next_index(&self, auto: bool) -> Option<usize> {
        if self.tracks.is_empty() {
            return None;
        }
        if auto && self.repeat == Repeat::One && self.current().is_some() {
            return Some(self.index as usize);
        }
        let nxt = (self.index + 1) as usize;
        if nxt < self.tracks.len() {
            return Some(nxt);
        }
        if self.repeat != Repeat::Off {
            return Some(0);
        }
        None
    }

    pub fn advance(&mut self, auto: bool) -> Option<&Track> {
        let i = self.next_index(auto)?;
        self.index = i as isize;
        self.touch();
        self.current()
    }

    pub fn back(&mut self) -> Option<&Track> {
        if self.tracks.is_empty() {
            return None;
        }
        if self.index > 0 {
            self.index -= 1;
        } else if self.repeat == Repeat::All {
            self.index = self.tracks.len() as isize - 1;
        }
        self.touch();
        self.current()
    }

    pub fn jump(&mut self, index: usize) -> Option<&Track> {
        if index < self.tracks.len() {
            self.index = index as isize;
            self.touch();
        }
        self.current()
    }

    // ------------------------------------------------------------- modificar
    pub fn replace(&mut self, tracks: &[Track], start: usize) -> Option<&Track> {
        self.tracks = tracks.iter().map(Track::copy_new).collect();
        self.original = None;
        self.index = if self.tracks.is_empty() {
            -1
        } else {
            start.min(self.tracks.len() - 1) as isize
        };
        if self.shuffle && !self.tracks.is_empty() {
            let first = self.tracks.remove(self.index as usize);
            let mut orig = vec![first.clone()];
            orig.extend(self.tracks.iter().cloned());
            self.original = Some(orig);
            self.tracks.shuffle(&mut rand::rng());
            self.tracks.insert(0, first);
            self.index = 0;
        }
        self.touch();
        self.current()
    }

    pub fn add(&mut self, tracks: &[Track]) {
        let new: Vec<Track> = tracks.iter().map(Track::copy_new).collect();
        if self.shuffle
            && let Some(o) = self.original.as_mut()
        {
            o.extend(new.iter().cloned());
        }
        self.tracks.extend(new);
        self.touch();
    }

    /// Inserta después de la actual. Devuelve el índice de la primera insertada.
    pub fn insert_next(&mut self, tracks: &[Track]) -> usize {
        let new: Vec<Track> = tracks.iter().map(Track::copy_new).collect();
        let pos = if self.index >= 0 {
            self.index as usize + 1
        } else {
            self.tracks.len()
        };
        if let Some(o) = self.original.as_mut() {
            o.extend(new.iter().cloned());
        }
        self.tracks.splice(pos..pos, new);
        self.touch();
        pos
    }

    pub fn remove(&mut self, index: usize) {
        if index >= self.tracks.len() {
            return;
        }
        let t = self.tracks.remove(index);
        if let Some(o) = self.original.as_mut() {
            o.retain(|x| x.uid != t.uid);
        }
        let i = index as isize;
        if i < self.index {
            self.index -= 1;
        } else if i == self.index {
            self.index = i - 1; // la siguiente en sonar será la que ocupó su lugar
        }
        self.touch();
    }

    pub fn move_item(&mut self, index: usize, delta: isize) -> usize {
        if index >= self.tracks.len() {
            return index;
        }
        let j = (index as isize + delta).clamp(0, self.tracks.len() as isize - 1) as usize;
        if j == index {
            return j;
        }
        let cur = self.current().map(|t| t.uid.clone());
        let t = self.tracks.remove(index);
        self.tracks.insert(j, t);
        if let Some(uid) = cur {
            self.index = self
                .tracks
                .iter()
                .position(|t| t.uid == uid)
                .map_or(-1, |p| p as isize);
        }
        self.touch();
        j
    }

    pub fn clear(&mut self, keep_current: bool) {
        let cur = if keep_current {
            self.current().cloned()
        } else {
            None
        };
        self.index = if cur.is_some() { 0 } else { -1 };
        self.tracks = cur.into_iter().collect();
        self.original = None;
        self.touch();
    }

    // ------------------------------------------------------------- aleatorio
    pub fn shuffle(&self) -> bool {
        self.shuffle
    }

    pub fn set_shuffle(&mut self, on: bool) {
        if on == self.shuffle {
            return;
        }
        self.shuffle = on;
        let cur = self.current().map(|t| t.uid.clone());
        if on {
            self.original = Some(self.tracks.clone());
            let split = (self.index + 1).max(0) as usize;
            self.tracks[split..].shuffle(&mut rand::rng());
        } else {
            if let Some(orig) = self.original.take() {
                let alive: std::collections::HashSet<String> =
                    self.tracks.iter().map(|t| t.uid.clone()).collect();
                self.tracks = orig
                    .into_iter()
                    .filter(|t| alive.contains(&t.uid))
                    .collect();
            }
            if let Some(uid) = cur {
                self.index = self.tracks.iter().position(|t| t.uid == uid).unwrap_or(0) as isize;
            }
        }
        self.touch();
    }

    pub fn cycle_repeat(&mut self) -> Repeat {
        self.repeat = match self.repeat {
            Repeat::Off => Repeat::All,
            Repeat::All => Repeat::One,
            Repeat::One => Repeat::Off,
        };
        self.touch();
        self.repeat
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tracks(n: usize) -> Vec<Track> {
        (0..n).map(|i| Track::new(format!("t{i}"))).collect()
    }

    fn titles(q: &PlayQueue) -> Vec<String> {
        q.tracks.iter().map(|t| t.title.clone()).collect()
    }

    #[test]
    fn navigation_and_repeat() {
        let mut q = PlayQueue::new();
        q.replace(&tracks(3), 0);
        assert_eq!(q.advance(false).unwrap().title, "t1");
        assert_eq!(q.advance(false).unwrap().title, "t2");
        assert!(q.advance(false).is_none());
        q.repeat = Repeat::All;
        assert_eq!(q.advance(false).unwrap().title, "t0");
        q.repeat = Repeat::One;
        assert_eq!(q.advance(true).unwrap().title, "t0");
        assert_eq!(q.advance(false).unwrap().title, "t1");
        q.repeat = Repeat::All;
        q.jump(0);
        assert_eq!(q.back().unwrap().title, "t2");
    }

    #[test]
    fn remove_current_keeps_next() {
        let mut q = PlayQueue::new();
        q.replace(&tracks(3), 1);
        q.remove(1);
        assert_eq!(q.index, 0);
        assert_eq!(q.advance(false).unwrap().title, "t2");
    }

    #[test]
    fn shuffle_restores_order() {
        let mut q = PlayQueue::new();
        q.replace(&tracks(20), 3);
        q.set_shuffle(true);
        assert_eq!(q.current().unwrap().title, "t3");
        assert_eq!(&titles(&q)[..4], &["t0", "t1", "t2", "t3"]);
        q.add(&tracks(1));
        q.set_shuffle(false);
        assert_eq!(q.current().unwrap().title, "t3");
        assert_eq!(
            titles(&q)[..20],
            (0..20).map(|i| format!("t{i}")).collect::<Vec<_>>()[..]
        );
        assert_eq!(q.len(), 21);
    }

    #[test]
    fn move_and_insert() {
        let mut q = PlayQueue::new();
        q.replace(&tracks(3), 0);
        assert_eq!(q.move_item(0, 5), 2);
        assert_eq!(q.current().unwrap().title, "t0");
        assert_eq!(q.index, 2);
        let pos = q.insert_next(&tracks(1));
        assert_eq!(pos, 3);
        q.clear(true);
        assert_eq!(titles(&q), vec!["t0"]);
    }
}
