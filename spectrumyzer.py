#!/usr/bin/python3
# -*- Mode: Python; indent-tabs-mode: t; python-indent: 4; tab-width: 4 -*-

import os
import impulse
import signal
import shutil
from configparser import ConfigParser

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib


class AttributeDict(dict):
	"""Dictionary with keys as attributes. Does nothing but easy reading."""
	def __getattr__(self, attr):
		return self[attr]

	def __setattr__(self, attr, value):
		self[attr] = value


class ConfigManager(dict):
	"""Read some program setting from file"""
	def __init__(self, configfile, winstate_valid=[]):
		self.winstate_valid = winstate_valid
		self.configfile = configfile
		if os.path.isfile("/usr/share/spectrumyzer/config"):
			self.defconfig = "/usr/share/spectrumyzer/config"
		else:
			self.defconfig = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

		if not os.path.isfile(configfile):
			shutil.copyfile(self.defconfig, configfile)
			print(
				"It seems you have started Spectrumyzer for the first time.\n"
				"New configuration file was created:\n%s" % self.configfile
			)

		self.parser = ConfigParser()
		try:
			# TODO: add logger module for colored output
			self.parser.read(configfile)
			self.read_spec_data()
		except Exception as e:
			print("Fail to read user config:")
			print(e)

			print("Trying with default config...")
			self.parser.read(self.defconfig)
			self.read_spec_data()
			print("Default config successfully loaded.")

	def read_spec_data(self):
		self["source"] = self.parser.getint("Main", "source")
		self["padding"] = self.parser.getint("Bars", "padding")
		self["scale"] = self.parser.getfloat("Bars", "scale")

		for key in ("left", "right", "top", "bottom"):
			self[key + "_offset"] = self.parser.getint("Offset", key)

		# color
		hex_ = self.parser.get("Bars", "rgba").lstrip("#")
		nums = [int(hex_[i:i + 2], 16) / 255.0 for i in range(0, 7, 2)]
		self["rgba"] = Gdk.RGBA(*nums)

		# window state
		self["state"] = [word.strip() for word in self.parser.get("Main", "state").split(",")]
		if not all(state in self.winstate_valid for state in self["state"]):
			raise Exception("Wrong window settings")


class WindowState:
	"""Window properties manager"""
	valid = ("normal", "desktop", "screensize", "fullscreen", "maximize", "keep_below", "skip_taskbar", "skip_pager")

	def __init__(self, window, screen_size):
		self.window = window

		self.actions = dict(
			normal = lambda: None,
			desktop = lambda: self.window.set_type_hint(Gdk.WindowTypeHint.DESKTOP),
			screensize = lambda: self.window.set_default_size(*screen_size),
			fullscreen = lambda: self.window.fullscreen(),
			maximize = lambda: self.window.maximize(),
			keep_below = lambda: self.window.set_keep_below(True),
			skip_taskbar = lambda: self.window.set_skip_taskbar_hint(True),
			skip_pager = lambda: self.window.set_skip_pager_hint(True),
		)

	def setup(self, *settings):
		"""Set wondow properties"""
		for state in settings:
			self.actions[state]()


class MainApp:
	"""Main application class"""
	def __init__(self):
		self.silence_value = 0
		self.audio_sample = []
		self.previous_sample = []  # this is formatted one so its len may be different from original

		# init window
		self.window = Gtk.Window()
		screen = self.window.get_screen()
		self.winstate = WindowState(self.window, [screen.get_width(), screen.get_height()])

		# load config
		self.configfile = os.path.expanduser("~/.config/spectrum.conf")
		self.config = ConfigManager(self.configfile, self.winstate.valid)

		# start spectrum analyzer
		impulse.setup(self.config["source"])
		impulse.start()

		# set window state according config settings
		self.winstate.setup(*self.config["state"])

		# set window transparent
		self.window.set_visual(screen.get_rgba_visual())
		self.window.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))

		# init drawing widget
		self.draw_area = Gtk.DrawingArea()
		self.draw_area.connect("draw", self.redraw)
		self.window.add(self.draw_area)

		# semi constants for drawing
		self.bars = AttributeDict()
		self.bars.padding = self.config["padding"]
		self.bars.number = 64

		# signals
		GLib.timeout_add(33, self.update)
		self.window.connect("delete-event", self.close)
		self.window.connect("check-resize", self.on_window_resize)

		# show window
		self.window.show_all()

	def is_silence(self, value):
		"""Check if volume level critically low during last iterations"""
		self.silence_value = 0 if value > 0 else self.silence_value + 1
		return self.silence_value > 10

	def on_window_resize(self, *args):
		"""Update drawing vars"""
		self.bars.win_width = self.draw_area.get_allocated_width() - self.config["right_offset"]
		self.bars.win_height = self.draw_area.get_allocated_height() - self.config["bottom_offset"]

		total_width = (self.bars.win_width - self.config["left_offset"]) - self.bars.padding * (self.bars.number - 1)
		self.bars.width = max(int(total_width / self.bars.number), 1)
		self.bars.height = self.bars.win_height - self.config["top_offset"]
		if "desktop" in self.config["state"]: self.bars.height /= 2
		self.bars.mark = total_width % self.bars.number  # width correnction point

	def update(self):
		"""Main update loop handler """
		self.audio_sample = impulse.getSnapshot(True)[:128]
		if not self.is_silence(self.audio_sample[0]):
			self.draw_area.queue_draw()
		return True

	def redraw(self, widget, cr):
		"""Draw spectrum graph"""
		cr.set_source_rgba(*self.config["rgba"])

		raw = list(map(lambda a, b: (a + b) / 2, self.audio_sample[::2], self.audio_sample[1::2]))
		if self.previous_sample == []:
			self.previous_sample = raw
		self.previous_sample = list(map(lambda p, r: p + (r - p) / 1.3, self.previous_sample, raw))

		dx = self.config["left_offset"]
		for i, value in enumerate(self.previous_sample):
			width = self.bars.width + int(i < self.bars.mark)
			height = self.bars.height * min(self.config["scale"] * value, 1)
			cr.rectangle(dx, self.bars.win_height, width, - height)
			dx += width + self.bars.padding
		cr.fill()

	def close(self, *args):
		"""Program exit"""
		Gtk.main_quit()


if __name__ == "__main__":
	signal.signal(signal.SIGINT, signal.SIG_DFL)  # make ^C work

	MainApp()
	Gtk.main()
	exit()
