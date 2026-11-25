plugins {
    id("com.android.application") version "9.5.2" apply false
    // AGP 9 compila Kotlin por sí mismo; este plugin fija la versión de Kotlin y activa Compose.
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.21" apply false
    id("com.chaquo.python") version "17.2.0" apply false
}
