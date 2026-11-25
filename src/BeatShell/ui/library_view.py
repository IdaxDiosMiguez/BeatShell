"""Vista de biblioteca: archivos locales + tu colección (carpetas, playlists, álbumes, artistas).

Se monta como mixin sobre la App para no tener un único archivo gigante.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import work
from textual.widgets import Input, Label, Tree

from ..library import ALBUM, ARTIST, FOLDER, KIND_LABEL, PLAYLIST, export_m3u, import_m3u
from ..models import Track, fmt_time
from ..services import safe_name
from ..sources import classify, spotify, youtube
from .screens import (
    NEW_FOLDER,
    NEW_PLAYLIST,
    ROOT,
    SAVE_ALBUM,
    SAVE_ARTIST,
    Confirm,
    FolderPicker,
    SaveMenu,
    TextPrompt,
)
from .widgets import LibraryTree, TrackTable, fold

ALL = ("all", None)


def _label(icon: str, name: str, sub: str = "") -> Text:
    t = Text(f"{icon} {name}", no_wrap=True, overflow="ellipsis")
    if sub:
        t.append(f"  {sub}", style="dim")
    return t


class LibraryMixin:
    """Toda la lógica de la vista Biblioteca. Requiere los atributos de BeatShellApp."""

    # Estado (se inicializa en BeatShellApp.__init__):
    #   _lib_sel: ("all", None) | ("dir", id_carpeta_local) | ("item", id_elemento)
    #   _lib_tracks: canciones de la selección, antes de filtrar
    #   _lib_expanded: ids de carpetas abiertas en el árbol

    # ---------------------------------------------------------------- selección
    @property
    def _sel_item(self):  # noqa: ANN201
        kind, ident = self._lib_sel
        return self.playlists.get(ident) if kind == "item" else None

    @property
    def _target_folder(self) -> str | None:
        """Carpeta donde crear cosas nuevas: la seleccionada o la del elemento seleccionado."""
        item = self._sel_item
        if item is None:
            return None
        return item.id if item.kind == FOLDER else item.parent

    # ---------------------------------------------------------------- árbol
    def _refresh_library_tree(self) -> None:
        tree = self.query_one("#lib-tree", LibraryTree)
        tree.clear()
        root = tree.root
        local = root.add(_label("♫", "Archivos locales", str(self.library.count())),
                         data=ALL, expand="__local__" in self._lib_expanded or not self.playlists.items)
        for f in self.library.folders:
            local.add_leaf(_label("▸", f.name, str(self.library.count(f.id))), data=("dir", f.id))

        def add_items(node, parent: str | None) -> None:  # noqa: ANN001
            for item in self.playlists.children(parent):
                if item.kind == FOLDER:
                    n = len(self.playlists.children(item.id))
                    child = node.add(_label("◫", item.name, str(n)), data=("item", item.id),
                                     expand=item.id in self._lib_expanded)
                    add_items(child, item.id)
                else:
                    sub = str(len(item.tracks)) if item.tracks else ""
                    node.add_leaf(_label(item.icon, item.name, sub), data=("item", item.id))

        add_items(root, None)
        # restaurar la selección
        target = None
        for node in self._walk(root):
            if node.data == self._lib_sel:
                target = node
                break
        if target is None:
            self._lib_sel = ALL
            target = local
        p = target.parent
        while p is not None and p is not root:
            p.expand()
            p = p.parent
        tree.move_cursor(target)
        # el árbol calcula sus líneas en el siguiente refresco: recolocamos el cursor entonces
        tree.call_after_refresh(tree.move_cursor, target)
        self._update_sidebar_stats()

    def _walk(self, node):  # noqa: ANN001, ANN202
        for child in node.children:
            yield child
            yield from self._walk(child)

    # Ojo: los manejadores van por nombre (on_<mensaje>) y no con @on(...): Textual solo
    # registra los @on de la propia clase App, no los de un mixin como este.
    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.control.id != "lib-tree":
            return
        # Al reconstruir el árbol llegan eventos de nodos viejos: solo vale el del cursor.
        if event.node is not event.control.cursor_node:
            return
        data = event.node.data
        if data and data != self._lib_sel:
            self._lib_sel = data
            self._show_library_selection()

    def on_library_tree_play(self, event: LibraryTree.Play) -> None:
        """Enter / doble clic en el árbol: suena todo lo que hay en ese elemento, desde el principio."""
        data = event.node.data
        if not data:
            return
        if data != self._lib_sel:
            self._lib_sel = data
            self._show_library_selection()
        tracks = self.query_one("#library-table", TrackTable).tracks  # respeta el filtro
        item = self._sel_item
        name = item.name if item is not None else "Archivos locales"
        if not tracks:
            msg = (f"Todavía estoy buscando canciones de {name}…"
                   if item is not None and item.kind == ARTIST else f"«{name}» no tiene canciones")
            self.notify(msg, timeout=3)
            return
        self.play_list(tracks, 0)
        self.notify(f"Reproduciendo «{name}» · {len(tracks)} canciones", timeout=3)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        if event.control.id != "lib-tree":
            return
        data = event.node.data
        self._lib_expanded.add("__local__" if data == ALL else (data[1] if data else ""))

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        if event.control.id != "lib-tree":
            return
        data = event.node.data
        self._lib_expanded.discard("__local__" if data == ALL else (data[1] if data else ""))

    # ---------------------------------------------------------------- contenido
    def _show_library_selection(self) -> None:
        kind, ident = self._lib_sel
        item = self._sel_item
        tbl = self.query_one("#library-table", TrackTable)
        ctx = "library"
        if kind == "all":
            tracks = self.library.tracks()
            title = "♫ Archivos locales · todas las canciones"
            hint = ("Enter reproducir · a cola · g guardar · f añadir carpeta de música · "
                    "R re-escanear")
            if not tracks and not self.library.folders:
                hint = ("Pulsa f para añadir una carpeta con música "
                        "(o pega/arrastra su ruta en la barra de arriba).")
        elif kind == "dir":
            f = self.library.folder(ident)
            tracks = self.library.tracks(ident)
            title = f"▸ {f.name if f else '?'}"
            hint = f"{f.path if f else ''} · e renombrar · x quitar de la biblioteca · R re-escanear"
        elif item is None:
            self._lib_sel = ALL
            return self._show_library_selection()
        elif item.kind == FOLDER:
            tracks = self.playlists.folder_tracks(item.id)
            ctx = "collection"
            n = len(self.playlists.children(item.id))
            title = f"◫ {self.playlists.path_of(item.id)}   {n} elementos"
            hint = ("Enter reproducir toda la carpeta · N playlist aquí · C subcarpeta · "
                    "M mover · e renombrar · x borrar")
        elif item.kind == ARTIST:
            local = [t for t in self.library.tracks() if fold(item.name) in fold(t.artist)]
            seen = {fold(t.title) for t in local}
            tracks = local + [t for t in item.tracks if fold(t.title) not in seen]
            ctx = "collection"
            title = f"★ {item.name}   {len(local)} locales · {len(tracks) - len(local)} en YouTube"
            hint = "Enter reproducir · D descargar todo · U actualizar canciones · M mover · x dejar de seguir"
        else:
            tracks = item.tracks
            ctx = "playlist" if item.kind == PLAYLIST else "collection"
            local_n = sum(1 for t in tracks if t.is_local)
            title = f"{item.icon} {item.name}"
            if item.kind == ALBUM and item.artist:
                title += f" — {item.artist}"
            title += f"   {len(tracks)} canciones · {fmt_time(sum(t.duration or 0 for t in tracks))}"
            if local_n:
                title += f" · {local_n} descargadas"
            hint = ("Enter reproducir · x quitar · Mayús↑↓ mover · D descargar · "
                    "^E exportar · M mover a carpeta · e renombrar"
                    if item.kind == PLAYLIST else
                    "Enter reproducir · D descargar el álbum · ^E exportar · M mover · x quitar")
        self._lib_tracks = tracks
        tbl.context = ctx
        self.query_one("#library-header", Label).update(title)
        self.query_one("#library-hint", Label).update(hint)
        self._apply_library_filter()

    def _apply_library_filter(self) -> None:
        q = fold(self.query_one("#library-filter", Input).value.strip())
        tracks = self._lib_tracks
        if q:
            words = q.split()
            tracks = [t for t in tracks
                      if all(w in fold(f"{t.title} {t.artist} {t.album}") for w in words)]
        tbl = self.query_one("#library-table", TrackTable)
        tbl.playing_key = self.queue.current.key if self.queue.current else None
        tbl.set_tracks(tracks, keep_cursor=False)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "library-filter":
            self._apply_library_filter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "library-filter":
            self.query_one("#library-table").focus()

    def _refresh_library(self) -> None:
        self._refresh_library_tree()
        self._show_library_selection()

    # ---------------------------------------------------------------- guardar
    def _open_save_menu(self, tracks: list[Track], what: str, single: Track | None = None) -> None:
        album = single.album if single and single.album else ""
        artist = (single.artist or "").split(",")[0].strip() if single else ""

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            if choice == SAVE_ALBUM and single:
                self._save_album(single)
            elif choice == SAVE_ARTIST and artist:
                self._save_artist(artist, single.thumb if single else None)
            elif choice == NEW_PLAYLIST:
                def named(name: str | None) -> None:
                    if name:
                        p = self.playlists.create(name, tracks, parent=self._target_folder)
                        self.notify(f"Playlist «{p.name}» creada con {len(tracks)} canción(es)")
                        self._refresh_library()
                self.push_screen(TextPrompt("Nombre de la nueva playlist", "Mi playlist"), named)
            else:
                n = self.playlists.add(choice, tracks)
                p = self.playlists.get(choice)
                self.notify(f"{n} canción(es) añadidas a «{p.name if p else ''}»", timeout=3)
                self._refresh_library()

        self.push_screen(SaveMenu(self.playlists, what, album, artist), chosen)

    def _save_album(self, track: Track) -> None:
        album = track.album
        artist = (track.artist or "").split(",")[0].strip()
        if self.playlists.find(ALBUM, album, artist):
            self.notify(f"El álbum «{album}» ya está en tu biblioteca", timeout=3)
            return
        if track.is_local:
            tracks = [t for t in self.library.tracks()
                      if t.album == album and (not artist or fold(artist) in fold(t.artist))]
            tracks.sort(key=lambda t: (t.track_no or 0, t.title.lower()))
        elif self._results and self._results.kind == "album" and self._results.name == album:
            tracks = self._results.tracks
        else:
            tracks = [track]
        p = self.playlists.create(album, tracks, kind=ALBUM, artist=artist, thumb=track.thumb,
                                  parent=None)
        self.notify(f"Álbum «{p.name}» guardado en tu biblioteca ({len(tracks)} canciones)")
        self._refresh_library()

    def _save_artist(self, name: str, thumb: str | None = None) -> None:
        if self.playlists.find(ARTIST, name):
            self.notify(f"Ya sigues a {name}", timeout=3)
            return
        p = self.playlists.create(name, kind=ARTIST, thumb=thumb)
        self.notify(f"Ahora sigues a {name}. Buscando sus canciones…", timeout=3)
        self._refresh_library()
        self._fetch_artist_tracks(p.id)

    def action_refresh_artist(self) -> None:
        item = self._sel_item
        if item and item.kind == ARTIST:
            self.notify(f"Actualizando canciones de {item.name}…", timeout=2)
            self._fetch_artist_tracks(item.id)

    @work(thread=True, group="artist")
    def _fetch_artist_tracks(self, pid: str) -> None:
        item = self.playlists.get(pid)
        if item is None:
            return
        try:
            found = youtube.search(f"{item.name} canciones", 30)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, str(exc), title="No se pudo buscar", severity="error")
            return
        key = fold(item.name)
        tracks, seen = [], set()
        for t in found:
            if key not in fold(f"{t.artist} {t.title}") or not t.duration or t.duration > 900:
                continue
            k = fold(t.title)
            if k not in seen:
                seen.add(k)
                tracks.append(t)
        self.call_from_thread(self._artist_tracks_ready, pid, tracks[:20])

    def _artist_tracks_ready(self, pid: str, tracks: list[Track]) -> None:
        item = self.playlists.get(pid)
        if item is None:
            return
        item.tracks = tracks
        self.playlists.save()
        if self._lib_sel == ("item", pid):
            self._show_library_selection()

    def _save_collection(self, col) -> object:  # noqa: ANN001
        """Guarda un álbum/playlist de YouTube o Spotify (o devuelve el que ya existía)."""
        existing = next((p for p in self.playlists.items if col.url and p.source_url == col.url), None)
        if existing:
            return existing
        kind = ALBUM if col.kind == "album" else PLAYLIST
        p = self.playlists.create(col.name, col.tracks, source_url=col.url, kind=kind,
                                  artist=col.owner if kind == ALBUM else "", thumb=col.thumb)
        self._refresh_library()
        return p

    # ---------------------------------------------------------------- organizar
    def action_new_playlist(self) -> None:
        parent = self._target_folder

        def named(name: str | None) -> None:
            if name:
                p = self.playlists.create(name, parent=parent)
                self._lib_sel = ("item", p.id)
                self._refresh_library()
                self.notify(f"Playlist «{p.name}» creada. Añade canciones con g desde cualquier lista.")
        self.push_screen(TextPrompt("Nueva playlist", "Nombre"), named)

    def action_new_folder(self) -> None:
        parent = self._target_folder

        def named(name: str | None) -> None:
            if name:
                f = self.playlists.create(name, kind=FOLDER, parent=parent)
                self._lib_sel = ("item", f.id)
                self._lib_expanded.add(f.id)
                self._refresh_library()
                self.notify(f"Carpeta «{f.name}» creada. Mueve cosas dentro con M.")
        self.push_screen(TextPrompt("Nueva carpeta", "Nombre", hint="Agrupa playlists, álbumes y "
                                    "artistas. Puedes meter carpetas dentro de carpetas."), named)

    def action_move_item(self) -> None:
        item = self._sel_item
        if item is None:
            self.notify("Selecciona en el árbol una playlist, álbum, artista o carpeta", timeout=3)
            return
        exclude = {item.id} | {d.id for d in self.playlists.descendants(item.id)}

        def chosen(dest: str | None) -> None:
            if dest is None:
                return
            if dest == NEW_FOLDER:
                def named(name: str | None) -> None:
                    if name:
                        f = self.playlists.create(name, kind=FOLDER)
                        self._move(item.id, f.id)
                self.push_screen(TextPrompt("Nueva carpeta", "Nombre"), named)
                return
            self._move(item.id, None if dest == ROOT else dest)

        what = f"{KIND_LABEL.get(item.kind, '').lower()} «{item.name}»"
        self.push_screen(FolderPicker(self.playlists, what, exclude), chosen)

    def _move(self, pid: str, folder: str | None) -> None:
        if self.playlists.move_to(pid, folder):
            if folder:
                self._lib_expanded.add(folder)
            self._refresh_library()
            where = self.playlists.path_of(folder) or "la raíz de la biblioteca"
            self.notify(f"Movido a {where}", timeout=2)

    def action_rename(self) -> None:
        kind, ident = self._lib_sel
        item = self._sel_item
        if item is not None:
            def done(name: str | None) -> None:
                if name:
                    self.playlists.rename(item.id, name)
                    self._refresh_library()
            self.push_screen(TextPrompt(f"Renombrar {KIND_LABEL[item.kind].lower()}",
                                        value=item.name), done)
        elif kind == "dir" and (f := self.library.folder(ident)):
            def done2(name: str | None) -> None:
                if name:
                    self.library.rename_folder(f.id, name)
                    self._refresh_library()
            self.push_screen(TextPrompt("Renombrar carpeta de música", value=f.name), done2)

    def _delete_selected(self) -> None:
        kind, ident = self._lib_sel
        item = self._sel_item
        if item is not None:
            if item.kind == FOLDER:
                q = f"¿Borrar la carpeta «{item.name}»?"
                detail = "Lo que contiene no se borra: sube un nivel."
            elif item.kind == ARTIST:
                q, detail = f"¿Dejar de seguir a {item.name}?", ""
            else:
                q = f"¿Quitar {KIND_LABEL[item.kind].lower()} «{item.name}» de la biblioteca?"
                detail = "Los archivos descargados no se borran."

            def ok(yes: bool) -> None:
                if yes:
                    self.playlists.delete(item.id)
                    self._lib_sel = ("item", item.parent) if item.parent else ALL
                    self._refresh_library()
            self.push_screen(Confirm(q, detail, "Quitar"), ok)
        elif kind == "dir" and (f := self.library.folder(ident)):
            def ok2(yes: bool) -> None:
                if yes:
                    self.library.remove_folder(f.id)
                    self._lib_sel = ALL
                    self._refresh_library()
            self.push_screen(Confirm(f"¿Quitar «{f.name}» de la biblioteca?",
                                     f"Solo se deja de mostrar; no se borra nada de {f.path}",
                                     "Quitar"), ok2)

    # ---------------------------------------------------------------- M3U8
    def action_export_playlist(self) -> None:
        item = self._sel_item
        if item is None or item.kind in (FOLDER, ARTIST):
            self.notify("Selecciona una playlist o un álbum para exportarlo", timeout=3)
            return
        default = Path(self.cfg.download_dir).expanduser() / "Playlists" / f"{safe_name(item.name)}.m3u8"

        def chosen(path: str | None) -> None:
            if not path:
                return
            try:
                out = export_m3u(item, path.strip().strip("'\""))
            except OSError as exc:
                self.notify(str(exc), title="No se pudo exportar", severity="error")
                return
            self.notify(f"Exportada a {out}", title="Playlist exportada", timeout=6)
        self.push_screen(TextPrompt("Exportar a .m3u8", value=str(default),
                                    hint="Se abre en VLC, foobar2000, Music, Winamp… "
                                         "Las canciones de internet se guardan como enlace."), chosen)

    def action_import_playlist(self) -> None:
        def chosen(text: str | None) -> None:
            if text:
                self._import_playlist(text)
        self.push_screen(TextPrompt(
            "Importar playlist", "Enlace de Spotify o /ruta/a/playlist.m3u8",
            hint="Spotify: enlace de una playlist, un álbum o un perfil (se importan todas sus "
                 "playlists públicas). También un .m3u/.m3u8: puedes arrastrarlo a la terminal."),
            chosen)

    def _import_playlist(self, path: str) -> None:
        if classify(path)[0] in ("spotify", "spotify_user"):
            self._import_spotify(path)
            return
        p = Path(path.strip().strip("'\"")).expanduser()
        try:
            name, tracks, missing = import_m3u(p)
        except OSError as exc:
            self.notify(str(exc), title="No se pudo importar", severity="error")
            return
        if not tracks:
            self.notify("Esa playlist no tiene canciones que se puedan abrir", severity="warning")
            return
        item = self.playlists.create(name, tracks, parent=self._target_folder)
        self._lib_sel = ("item", item.id)
        self.action_view("library")
        self._refresh_library()
        msg = f"«{item.name}» importada con {len(tracks)} canciones"
        if missing:
            msg += f" ({missing} archivos no se encontraron)"
        self.notify(msg, title="Playlist importada", timeout=6)

    # ---------------------------------------------------------------- Spotify
    def _import_spotify(self, link: str) -> None:
        user = classify(link)[0] == "spotify_user"
        self.notify("Leyendo el perfil de Spotify…" if user else "Leyendo Spotify…", timeout=3)
        self._do_import_spotify(link, self._target_folder)

    @work(thread=True, group="spotify-import", exclusive=True)
    def _do_import_spotify(self, link: str, parent: str | None) -> None:
        def progress(i: int, n: int, name: str) -> None:
            self.call_from_thread(self.notify, f"{i + 1}/{n} · {name}", title="Importando de Spotify",
                                  timeout=2)
        try:
            res = spotify.import_link(link, self.cfg.spotify_client_id,
                                      self.cfg.spotify_client_secret, progress)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, str(exc), title="No se pudo importar", severity="error")
            return
        self.call_from_thread(self._spotify_imported, res, parent)

    def _spotify_imported(self, res, parent: str | None) -> None:  # noqa: ANN001
        prof = res.profile
        item, created, updated = self.playlists.import_collections(
            res.collections, parent=parent,
            folder=prof.name if prof else None, folder_url=prof.url if prof else None,
            folder_thumb=prof.thumb if prof else None)
        if item is not None:
            self._lib_sel = ("item", item.id)
            if item.kind == FOLDER:
                self._lib_expanded.add(item.id)
        self.action_view("library")
        self._refresh_library()
        if prof is None:
            col = res.collections[0]
            msg = f"«{col.name}»: {len(col.tracks)} canciones"
            if updated:
                msg += " (ya la tenías: actualizada)"
            if len(col.tracks) >= spotify.EMBED_LIMIT and not self.cfg.spotify_client_id:
                msg += ". Sin credenciales de API Spotify solo da las primeras 100"
        else:
            n = len(res.collections)
            msg = f"{n} playlist{'s' if n != 1 else ''} de «{prof.name}» en la carpeta «{item.name}»"
            if updated:
                msg += f" ({updated} actualizadas)"
            if res.failed:
                msg += f". No se pudieron leer: {', '.join(res.failed)}"
            if prof.total > len(prof.playlists):
                msg += (f". Tiene {prof.total} públicas; sin credenciales de API solo se leen las "
                        f"{len(prof.playlists)} primeras (mira el README)")
        self.notify(msg, title="Importado de Spotify", timeout=10,
                    severity="warning" if res.failed else "information")
