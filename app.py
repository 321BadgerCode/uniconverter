import os
import sys
import subprocess
import uuid
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
CONVERTED_FOLDER = "converted"

# Declare supported file extensions
image_exts = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "ico", "svg"]
audio_exts = ["mp3", "wav", "flac", "aac", "m4a", "ogg"]
video_exts = ["mp4", "mov", "avi", "webm", "mkv", "flv", "wmv"]
doc_exts = ["pdf", "txt"]

# Declare command line argument variable
is_backup_enabled = False

# Create folders if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
if os.geteuid() == 0:
	os.chmod(UPLOAD_FOLDER, 0o777)
os.makedirs(CONVERTED_FOLDER, exist_ok=True)
if os.geteuid() == 0:
	os.chmod(CONVERTED_FOLDER, 0o777)

@app.route('/')
def index():
	"""
	Render the main page of the application.
	"""
	return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
	"""
	Handle file upload.
	"""
	file = request.files["file"]
	if file:
		filename = secure_filename(file.filename)
		ext = os.path.splitext(filename)[1].lower()[1::] # Get the file extension without the dot
		path = os.path.join(UPLOAD_FOLDER, filename)
		file.save(path)

		file_type = detect_type(ext)
		return {"filename": filename, "type": file_type}
	return {"error": "No file uploaded"}

@app.route("/convert", methods=["POST"])
def convert():
	"""
	Handle file conversion.
	"""
	data = request.form
	filename = data["filename"]
	target_format = data["target_format"]
	input_path = os.path.join(UPLOAD_FOLDER, filename)
	ext = os.path.splitext(filename)[1].lower()[1::] # Get the file extension without the dot
	base = uuid.uuid4().hex
	output_filename = f"{base}.{target_format}"
	output_path = os.path.join(CONVERTED_FOLDER, output_filename)

	if ext in image_exts and target_format in image_exts:
		try:
			from PIL import Image
			img = Image.open(input_path)
			# Pillow only knows jpeg, not jpg
			if target_format in ["jpg", "jpeg"]:
				target_format = "jpeg"
			# For ico files, save image with correct size
			elif target_format == "ico":
				img = img.convert("RGBA")

				# Ensure the image is square
				if img.size[0] != img.size[1]:
					min_size = min(img.size)
					img = img.resize((min_size, min_size), Image.Resampling.LANCZOS)
				# Resize into closest 32 multiple square
				size = 32
				while size < img.size[0]:
					size *= 2
				img = img.resize((size, size), Image.Resampling.LANCZOS)
				img.save(output_path, target_format.upper())
			# For SVG, we need to vectorize
			elif target_format == "svg":
				try:
					import cv2
					import svgwrite
					import numpy as np

					# Convert image to numpy array
					img_array = np.array(img)

					# Convert to grayscale
					gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

					# Threshold the image to get a binary image
					_, binary = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)

					# Find contours
					contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

					# Create SVG canvas
					height, width = binary.shape
					dwg = svgwrite.Drawing(output_path, size=(width, height))

					# Draw contours
					for contour in contours:
						points = [(int(x), int(y)) for [[x, y]] in contour]
						if len(points) > 2:
							dwg.add(dwg.polygon(points, fill='black', stroke='black'))
					dwg.save()
				except ImportError as e:
					print("Vectorizer error: vectorizer module not found")
					print("Error details:", e)
					return jsonify({"error": "vectorizer is required for SVG conversion."}), 500
			else:
				img = img.convert("RGB")
				img.save(output_path, target_format.upper())
		except ImportError as e:
			print("Pillow error: PIL module not found")
			print("Error details:", e)
			return jsonify({"error": "Pillow is required for image conversion."}), 500

	elif (ext in audio_exts and target_format in audio_exts) or (ext in video_exts and target_format in audio_exts):
		args = ["ffmpeg", "-y", "-i", input_path, "-vn", output_path]

		out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		if out.returncode != 0:
			print("FFmpeg error:", out.stderr)
			return jsonify({"error": "Audio conversion failed."}), 500

	elif ext in video_exts and target_format in video_exts:
		args = ["ffmpeg", "-y", "-i", input_path, output_path]

		try:
			subprocess.run(args, check=True)
		except subprocess.CalledProcessError as e:
			print("FFmpeg error:", e)
			return jsonify({"error": "Video conversion failed."}), 500

	elif ext in doc_exts and target_format in doc_exts:
		if ext == "pdf" and target_format == "txt":
			try:
				from pdfminer.high_level import extract_text
				text = extract_text(input_path)
				with open(output_path, 'w', encoding="utf-8") as f:
					f.write(text)
			except ImportError as e:
				print("PDFMiner error: pdfminer.six module not found")
				print("Error details:", e)
				return jsonify({"error": "pdfminer.six is required for PDF to TXT conversion."}), 500
		elif ext == "txt" and target_format == "pdf":
			try:
				from fpdf import FPDF
				pdf = FPDF()
				pdf.add_page()
				pdf.set_font("Arial", size=12)
				with open(input_path, 'r', encoding="utf-8") as f:
					for line in f:
						pdf.cell(200, 10, txt=line, ln=True)
				pdf.output(output_path)
			except ImportError as e:
				print("FPDF error: fpdf module not found")
				print("Error details:", e)
				return jsonify({"error": "fpdf is required for TXT to PDF conversion."}), 500
		else:
			return jsonify({"error": "Unsupported document conversion"}), 400

	else:
		return {"error": "Unsupported conversion"}

	return send_file(output_path, as_attachment=True)

def detect_type(ext):
	"""
	Detect the type of file based on its extension.
	"""
	if ext in image_exts:
		return "image"
	elif ext in audio_exts:
		return "audio"
	elif ext in video_exts:
		return "video"
	elif ext in doc_exts:
		return "document"
	else:
		return "unknown"

def cleanup_files():
	"""
	Cleanup uploaded and converted files.
	"""
	for folder in [UPLOAD_FOLDER, CONVERTED_FOLDER]:
		for filename in os.listdir(folder):
			file_path = os.path.join(folder, filename)
			try:
				if os.access(file_path, os.W_OK):
					os.remove(file_path)
			except Exception as e:
				print(f"Error deleting {file_path}: {e}")

if __name__ == "__main__":
	if "-h" in sys.argv or "--help" in sys.argv:
		print(f"Usage: python {sys.argv[0]} [--cleanup]")
		print("\nOptions:")
		print("  --cleanup\t\tDelete uploaded and converted files on exit")
		sys.exit(0)
	elif "--cleanup" in sys.argv:
		os.environ["UNICONVERTER_CLEANUP"] = '1'

	if os.environ.get("UNICONVERTER_CLEANUP") == '1':
		import signal
		signal.signal(signal.SIGTERM, lambda signum, frame: cleanup_files())
		signal.signal(signal.SIGINT, lambda signum, frame: cleanup_files())

	app.run(host='0.0.0.0', port=5000)