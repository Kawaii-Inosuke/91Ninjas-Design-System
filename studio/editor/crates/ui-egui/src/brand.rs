//! The VectorCraft brand mark: the app icon (the engraved dragon, `assets/app-icon/`, see its
//! README). The PNG carries the icon's rounded corners; it is decoded once per context into a
//! mipmapped texture, so it stays crisp from the 18 pt app-bar mark to the About box.

use egui::{Color32, Context, Id, Rect, TextureHandle, TextureOptions, Ui, pos2};

/// 128 px: sharp at 44 pt on a 2× display, and the mipmaps keep 22 pt clean.
// 91studio: our icon (assets/91studio, placeholder until the 91Ninjas logo arrives; D-022).
const ICON_PNG: &[u8] = include_bytes!("../../../assets/91studio/icon-128.png");

/// 91studio: the 91NINJAS wordmark, white on transparent; painted in the theme's text colour (D-022).
const WORDMARK_PNG: &[u8] = include_bytes!("../../../assets/91studio/91ninjas-wordmark.png");

/// The icon's pixels (one transparent pixel if it couldn't be decoded, which a test rules out).
fn decode() -> egui::ColorImage {
    decode_png(ICON_PNG)
}

fn decode_png(png: &[u8]) -> egui::ColorImage {
    match image::load_from_memory_with_format(png, image::ImageFormat::Png) {
        Ok(img) => {
            let img = img.to_rgba8();
            egui::ColorImage::from_rgba_unmultiplied([img.width() as usize, img.height() as usize], img.as_raw())
        }
        Err(_) => egui::ColorImage::filled([1, 1], Color32::TRANSPARENT),
    }
}

/// The icon texture, uploaded on first use and kept in the context.
fn texture(ctx: &Context) -> TextureHandle {
    let id = Id::unique("vectorcraft-brand-mark");
    if let Some(tex) = ctx.data(|d| d.get_temp::<TextureHandle>(id)) {
        return tex;
    }
    let options = TextureOptions { mipmap_mode: Some(egui::TextureFilter::Linear), ..TextureOptions::LINEAR };
    let tex = ctx.load_texture("vectorcraft-brand-mark", decode(), options);
    ctx.data_mut(|d| d.insert_temp(id, tex.clone()));
    tex
}

/// 91studio: the wordmark texture, uploaded on first use and kept in the context.
fn wordmark_texture(ctx: &Context) -> TextureHandle {
    let id = Id::unique("studio-wordmark");
    if let Some(tex) = ctx.data(|d| d.get_temp::<TextureHandle>(id)) {
        return tex;
    }
    let options = TextureOptions { mipmap_mode: Some(egui::TextureFilter::Linear), ..TextureOptions::LINEAR };
    let tex = ctx.load_texture("studio-wordmark", decode_png(WORDMARK_PNG), options);
    ctx.data_mut(|d| d.insert_temp(id, tex.clone()));
    tex
}

/// 91studio: the 91NINJAS wordmark, `height` points tall, in `tint`.
pub fn wordmark(ui: &mut Ui, height: f32, tint: Color32) {
    let tex = wordmark_texture(ui.ctx());
    let s = tex.size_vec2();
    let (r, _) = ui.allocate_exact_size(egui::vec2(height * s.x / s.y.max(1.0), height), egui::Sense::hover());
    ui.painter().image(tex.id(), r, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), tint);
}

/// Paint the mark into `r` (square).
pub fn paint_mark(ui: &Ui, r: Rect) {
    let uv = Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0));
    ui.painter().image(texture(ui.ctx()).id(), r, uv, Color32::WHITE);
}

/// 91studio: the wordmark's texture id (tests: the app bar paints it).
#[cfg(test)]
pub fn wordmark_texture_id(ctx: &Context) -> egui::TextureId {
    wordmark_texture(ctx).id()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::VectorcraftApp;

    #[test]
    fn the_icon_decodes_to_a_rounded_128_px_tile() {
        let img = decode();
        assert_eq!(img.size, [128, 128]);
        // Rounded corners: the corner pixel is transparent, the centre opaque.
        assert_eq!(img.pixels[0].a(), 0);
        assert_eq!(img.pixels[64 * 128 + 64].a(), 255);
    }

    #[test]
    // 91studio: the app bar shows the wordmark (D-025), still from one cached texture.
    fn the_app_bar_paints_the_icon_from_one_cached_texture() {
        let ctx = Context::default();
        crate::theme::install_fonts(&ctx);
        let mut app = VectorcraftApp::new(vectorcraft_engine::Session::new(), Default::default());
        // The system title bar hides the in-app mark (the OS draws its own icon), so paint the
        // app-drawn bar here.
        app.custom_titlebar = true;
        let mut frame = || {
            let mut out = ctx.run_ui(egui::RawInput::default(), |ui| crate::chrome::app_bar(&mut app, ui));
            let uploads = out.textures_delta.set.values().flat_map(|d| d.iter()).filter(|d| d.image.size() == [1394, 210]).count();
            out.textures_delta.clear();
            let marks: Vec<Rect> = out
                .shapes
                .iter()
                .filter_map(|c| match &c.shape {
                    egui::Shape::Mesh(m) if m.texture_id == wordmark_texture(&ctx).id() => Some(m.calc_bounds()),
                    _ => None,
                })
                .collect();
            (uploads, marks)
        };
        let (uploads, marks) = frame();
        assert_eq!(uploads, 1);
        assert_eq!(marks.len(), 1);
        assert!((marks[0].height() - 14.4).abs() < 0.5 && marks[0].width() > 90.0, "{:?}", marks[0]);
        // Later frames reuse the texture.
        let (uploads, marks) = frame();
        assert_eq!((uploads, marks.len()), (0, 1));
    }
}
