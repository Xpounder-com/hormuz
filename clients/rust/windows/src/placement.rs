#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

pub fn scale(value: i32, dpi: u32) -> i32 {
    (i64::from(value) * i64::from(dpi.clamp(48, 768)) / 96) as i32
}

/// Work-area coordinates can be negative on secondary monitors. Bound the
/// entire outer window, including its caption, inside the current work area.
pub fn place(area: Rect, width: i32, height: i32, margin: i32) -> Rect {
    let available_width = area.right.saturating_sub(area.left).max(1);
    let available_height = area.bottom.saturating_sub(area.top).max(1);
    let width = width.clamp(1, available_width);
    let height = height.clamp(1, available_height);
    let x_margin = margin.clamp(0, available_width - width);
    let y_margin = margin.clamp(0, available_height - height);
    let left = area.right - width - x_margin;
    let top = area.top + y_margin;
    Rect {
        left,
        top,
        right: left + width,
        bottom: top + height,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn taskbars_negative_monitors_and_small_work_areas_keep_the_window_reachable() {
        for area in [
            Rect {
                left: 0,
                top: 0,
                right: 1920,
                bottom: 1040,
            },
            Rect {
                left: 80,
                top: 0,
                right: 1920,
                bottom: 1080,
            },
            Rect {
                left: -1920,
                top: -300,
                right: 0,
                bottom: 740,
            },
            Rect {
                left: 20,
                top: 30,
                right: 180,
                bottom: 140,
            },
        ] {
            for dpi in [96, 144, 192, 288] {
                for height in [106, 216] {
                    let rect = place(area, scale(336, dpi), scale(height, dpi), scale(12, dpi));
                    assert!(rect.left >= area.left && rect.right <= area.right);
                    assert!(rect.top >= area.top && rect.bottom <= area.bottom);
                    assert!(rect.right > rect.left && rect.bottom > rect.top);
                }
            }
        }
    }
}
