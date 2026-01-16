import subprocess
import time
import threading
import sys

class LinuxMP3Player:
    def __init__(self, file_path):
        self.file_path = file_path
        self.playing = False
        self.start_time = 0
        self.elapsed_offset = 0
        self.process = None

    def play(self):
        # We use ffplay because it's standard on Linux for decoding
        # -nodisp (no video), -autoexit (close when done)
        self.process = subprocess.Popen(
            ['ffplay', '-nodisp', '-autoexit', '-i', self.file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        self.playing = True
        self.start_time = time.time()

    def toggle_pause(self):
        if self.playing:
            # Send 'SIGSTOP' to freeze the process
            self.process.send_signal(20) # 20 is SIGSTOP
            self.elapsed_offset += time.time() - self.start_time
            self.playing = False
        else:
            # Send 'SIGCONT' to resume
            self.process.send_signal(18) # 18 is SIGCONT
            self.start_time = time.time()
            self.playing = True

    def get_current_time(self):
        if not self.playing:
            return self.elapsed_offset
        return self.elapsed_offset + (time.time() - self.start_time)

# --- TUI LOOP ---
player = LinuxMP3Player(sys.argv[1])
player.play()

try:
    while True:
        curr = player.get_current_time()
        sys.stdout.write(f"\rTime: {curr:.1f}s | [P] Pause/Resume [Q] Quit: ")
        sys.stdout.flush()
        
        # This is a simple way to get input without stopping the timer
        # In a real TUI, you'd use 'select' for non-blocking input
        choice = input().lower()
        if choice == 'p':
            player.toggle_pause()
        elif choice == 'q':
            player.process.kill()
            break
except KeyboardInterrupt:
    player.process.kill()