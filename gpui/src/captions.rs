/// Match the native canvas: fill up to three lines, then start a new page.
/// Measurements include a word's trailing space while wrapping uses its ink width.
pub fn page(text: &str, width: f32, capacity: usize, measure: impl Fn(&str) -> f32) -> Vec<String> {
    let mut lines = vec![String::new()];
    let capacity = capacity.max(1);
    for word in text.split_inclusive(char::is_whitespace) {
        let mut pieces = Vec::new();
        if measure(word) > width {
            let mut part = String::new();
            for character in word.chars() {
                let candidate = format!("{part}{character}");
                if !part.is_empty() && measure(&candidate) > width {
                    pieces.push(std::mem::take(&mut part));
                }
                part.push(character);
            }
            if !part.is_empty() {
                pieces.push(part);
            }
        } else {
            pieces.push(word.to_owned());
        }
        for piece in pieces {
            let last = lines.last_mut().unwrap();
            if !last.is_empty() && measure(last) + measure(piece.trim_end()) > width {
                if lines.len() >= capacity {
                    lines = vec![String::new()];
                } else {
                    lines.push(String::new());
                }
            }
            lines.last_mut().unwrap().push_str(&piece);
        }
    }
    lines
}
#[cfg(test)]
mod tests {
    use super::page;
    fn measure(value: &str) -> f32 {
        value.chars().count() as f32
    }
    #[test]
    fn sentence_pages_do_not_scroll_or_discard_the_start_of_a_new_page() {
        assert_eq!(page("one two three four five", 10., 2, measure), ["five"]);
        assert_eq!(
            page("one two three four", 10., 2, measure),
            ["one two ", "three four"]
        );
    }
    #[test]
    fn unbroken_unicode_words_fit_without_splitting_code_points() {
        let lines = page("éééééééé", 3., 3, measure);
        assert_eq!(lines, ["ééé", "ééé", "éé"]);
    }
}
