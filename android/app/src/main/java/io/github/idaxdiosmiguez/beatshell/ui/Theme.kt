package io.github.idaxdiosmiguez.beatshell.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/** Mismos colores que la versión de escritorio (desktop/beatshell_desktop/style.py). */
@Immutable
data class BsColors(
    val dark: Boolean,
    val bg: Color,
    val side: Color,
    val hover: Color,
    val selected: Color,
    val border: Color,
    val text: Color,
    val muted: Color,
    val faint: Color,
    val accent: Color,
    val accentText: Color,
    val error: Color,
)

val DarkColors = BsColors(
    dark = true, bg = Color(0xFF0E0E11), side = Color(0xFF0A0A0C), hover = Color(0xFF18181D),
    selected = Color(0xFF212128), border = Color(0xFF1C1C22), text = Color(0xFFF2F2F5),
    muted = Color(0xFF8D8D99), faint = Color(0xFF55555F), accent = Color(0xFFFF4FD8),
    accentText = Color(0xFF16060F), error = Color(0xFFFF5C7A),
)

val LightColors = BsColors(
    dark = false, bg = Color(0xFFFFFFFF), side = Color(0xFFF6F6F8), hover = Color(0xFFF0F0F3),
    selected = Color(0xFFE9E9EF), border = Color(0xFFE7E7EC), text = Color(0xFF141418),
    muted = Color(0xFF6C6C78), faint = Color(0xFFA6A6B0), accent = Color(0xFFD81FB6),
    accentText = Color(0xFFFFFFFF), error = Color(0xFFD93355),
)

val Cyan = Color(0xFF5EE7FF)

val LocalBs = staticCompositionLocalOf { DarkColors }

@Composable
fun BeatShellTheme(content: @Composable () -> Unit) {
    val c = if (isSystemInDarkTheme()) DarkColors else LightColors
    val scheme = if (c.dark) {
        darkColorScheme(
            primary = c.accent, onPrimary = c.accentText, secondary = c.accent, onSecondary = c.accentText,
            background = c.bg, onBackground = c.text, surface = c.bg, onSurface = c.text,
            surfaceVariant = c.hover, onSurfaceVariant = c.muted, surfaceContainer = c.selected,
            surfaceContainerHigh = c.selected, surfaceContainerHighest = c.hover, surfaceContainerLow = c.side,
            outline = c.faint, outlineVariant = c.border, error = c.error, inverseSurface = c.text,
            inverseOnSurface = c.bg,
        )
    } else {
        lightColorScheme(
            primary = c.accent, onPrimary = c.accentText, secondary = c.accent, onSecondary = c.accentText,
            background = c.bg, onBackground = c.text, surface = c.bg, onSurface = c.text,
            surfaceVariant = c.hover, onSurfaceVariant = c.muted, surfaceContainer = c.side,
            surfaceContainerHigh = c.side, surfaceContainerHighest = c.hover, surfaceContainerLow = c.side,
            outline = c.faint, outlineVariant = c.border, error = c.error, inverseSurface = c.text,
            inverseOnSurface = c.bg,
        )
    }
    CompositionLocalProvider(LocalBs provides c) {
        MaterialTheme(colorScheme = scheme, content = content)
    }
}
