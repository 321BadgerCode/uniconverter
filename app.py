import os
import sys
import subprocess
import uuid
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
import zipfile
import tarfile
import tempfile

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
CONVERTED_FOLDER = "converted"

# Declare supported file extensions
image_exts = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "ico", "svg", "eps", "tga", "tif", "tiff", "ppm", "xbm", "icns"]
audio_exts = ["mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aiff", "alac", "amr", "mka"]
video_exts = ["mp4", "mov", "avi", "webm", "mkv", "flv", "wmv", "mpeg", "mpg", "3gp", "m4v", "ts"]
doc_exts = ["pdf", "txt"]
archive_exts = ["zip", "rar", "tar", "gz", "7z", "bz2", "xz"]

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

def image_to_colored_svg_kmeans(image_path, output_svg, num_colors=8, min_region_size=100):
	"""
	Convert an image to a colored SVG using K-means clustering.
	Args:
		image_path (str): Path to the input image.
		output_svg (str): Path to save the output SVG file.
		num_colors (int): Number of colors to reduce the image to.
		min_region_size (int): Minimum size of regions to include in the SVG.
	"""
	import cv2
	import numpy as np
	import svgwrite
	from collections import defaultdict

	img = cv2.imread(image_path)
	img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
	h, w, _ = img_rgb.shape

	# Flatten image for K-means clustering
	Z = img_rgb.reshape((-1, 3))
	Z = np.float32(Z)

	# Apply K-means to reduce colors
	criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
	_, labels, centers = cv2.kmeans(Z, num_colors, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)

	# Convert back to image with reduced colors
	centers = np.uint8(centers)
	reduced_img = centers[labels.flatten()].reshape(img_rgb.shape)

	# Map each color to a mask
	masks = defaultdict(lambda: np.zeros((h, w), dtype=np.uint8))
	label_img = labels.reshape((h, w))

	for i, color in enumerate(centers):
		masks[i][label_img == i] = 255

	# Create SVG
	dwg = svgwrite.Drawing(output_svg, size=(w, h))

	for i, mask in masks.items():
		contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
		color = centers[i]
		fill = svgwrite.rgb(color[0], color[1], color[2])

		for contour in contours:
			if cv2.contourArea(contour) < min_region_size:
				continue

			points = [(int(p[0][0]), int(p[0][1])) for p in contour]
			if len(points) >= 3:
				dwg.add(dwg.polygon(points, fill=fill, stroke="black", stroke_width=0.2))

	dwg.save()

def zip_to_tar(zip_path, tar_path):
	"""
	Convert a zip archive to a tar.gz archive.
	Args:
		zip_path (str): Path to the input zip file.
		tar_path (str): Path to save the output tar.gz file.
	"""
	with zipfile.ZipFile(zip_path, 'r') as zip_ref:
		with tempfile.TemporaryDirectory() as temp_dir:
			zip_ref.extractall(temp_dir)

			with tarfile.open(tar_path, 'w:gz') as tar:
				tar.add(temp_dir, arcname='.')

def sevenz_to_zip(sevenz_path, zip_path):
	import py7zr
	"""
	Convert a 7z archive to a zip archive.
	Args:
		sevenz_path (str): Path to the input 7z file.
		zip_path (str): Path to save the output zip file.
	"""
	with py7zr.SevenZipFile(sevenz_path, mode='r') as archive:
		with zipfile.ZipFile(zip_path, 'w') as zipf:
			for file in archive.getnames():
				data = archive.read(file)
				zipf.writestr(file, data)

def convert_one(file):
	"""
	Convert a single file.
	Args:
		file (dict): A dictionary containing the filename and target format.
	"""
	filename = file["filename"]
	ext = os.path.splitext(filename)[1].lower()[1::] # Get the file extension without the dot
	target_format = file["target_format"]
	input_path = os.path.join(UPLOAD_FOLDER, filename)
	base = uuid.uuid4().hex
	output_filename = f"{base}.{target_format}"
	output_path = os.path.join(CONVERTED_FOLDER, output_filename)

	# If asking to convert to the same format, just return the original file
	if ext == target_format:
		return {"filename": filename, "type": detect_type(ext)}

	if ext in image_exts and target_format in image_exts:
		try:
			from PIL import Image

			# For SVG, we need to vectorize the image
			if target_format == "svg":
				try:
					image_to_colored_svg_kmeans(input_path, output_path)
				except ImportError as e:
					print("Import errors: cv2, numpy, or svgwrite module not found")
					print("Error details:", e)
					return jsonify({"error": "Required libraries for SVG conversion are not installed."}), 500

			img = Image.open(input_path)

			# Pillow only knows jpeg, not jpg
			if target_format == "jpg":
				target_format = "jpeg"
			# Pillow only knows tiff, not tif
			elif target_format == "tif":
				target_format = "tiff"

			# If target format takes RGB, convert to RGB
			if target_format in ["jpeg", "eps", "ppm"] and img.mode != "RGB":
				img = img.convert("RGB")
			# If target format takes RGBA, convert to RGBA
			elif target_format in ["png", "webp", "bmp", "ico", "tga", "tiff", "icns"] and img.mode != "RGBA":
				img = img.convert("RGBA")
			# If target format takes P, convert to P
			elif target_format in ["gif"] and img.mode != "P":
				img = img.convert("P")
			# If target format takes 1, convert to 1
			elif target_format in ["xbm"] and img.mode != "1":
				img = img.convert("1")

			# For ico files, save image with correct size
			if target_format == "ico":
				# Ensure the image is square
				if img.size[0] != img.size[1]:
					min_size = min(img.size)
					img = img.resize((min_size, min_size), Image.Resampling.LANCZOS)
				# Resize into closest 32 multiple square
				size = 32
				while size < img.size[0]:
					size *= 2
				img = img.resize((size, size), Image.Resampling.LANCZOS)

			# Save the image in the target format
			if not target_format in ["svg", "heic"]:
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

	elif ext in audio_exts and target_format in video_exts:
		args = ["ffmpeg", "-y", "-i", input_path, "-c:a", "aac", output_path]

		try:
			subprocess.run(args, check=True)
		except subprocess.CalledProcessError as e:
			print("FFmpeg error:", e)
			return jsonify({"error": "Audio to video conversion failed."}), 500

	elif ext in video_exts and target_format in video_exts:
		args = ["ffmpeg", "-y", "-i", input_path, output_path]

		try:
			subprocess.run(args, check=True)
		except subprocess.CalledProcessError as e:
			print("FFmpeg error:", e)
			return jsonify({"error": "Video conversion failed."}), 500

	elif ext in video_exts and target_format in image_exts:
		args = ["ffmpeg", "-y", "-i", input_path, "-frames:v", "1", output_path]

		try:
			subprocess.run(args, check=True)
		except subprocess.CalledProcessError as e:
			print("FFmpeg error:", e)
			return jsonify({"error": "Video to image conversion failed."}), 500

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

	elif ext == "pdf" and target_format in image_exts:
		try:
			from pdf2image import convert_from_path
			pages = convert_from_path(input_path)
			for i, page in enumerate(pages):
				page_output_filename = f"{base}_{i + 1}.png"
				page_output_path = os.path.join(UPLOAD_FOLDER, page_output_filename)
				page.save(page_output_path, "PNG")
			# Convert the pages to the target format using convert one
			tmp_filenames = []
			if target_format != "png":
				for i, page in enumerate(pages):
					page_output_filename = f"{base}_{i + 1}.png"
					tmp_filenames.append(convert_one({"filename": page_output_filename, "target_format": target_format}))
			# Zip all of the files into a single file
			if len(tmp_filenames) > 1:
				zip_filename = f"{base}_pages.zip"
				zip_path = os.path.join(CONVERTED_FOLDER, zip_filename)
				with zipfile.ZipFile(zip_path, 'w') as zipf:
					for i in tmp_filenames:
						zipf.write(os.path.join(CONVERTED_FOLDER, i), arcname=i)
				return zip_filename
			else:
				os.rename(os.path.join(UPLOAD_FOLDER, f"{base}_1.png"), os.path.join(CONVERTED_FOLDER, output_filename))
				return output_filename
		except ImportError as e:
			print("PDF2Image error: pdf2image module not found")
			print("Error details:", e)
			return jsonify({"error": "pdf2image is required for PDF to image conversion."}), 500

	elif ext in archive_exts and target_format in archive_exts:
		if ext == "zip" and target_format == "gz":
			zip_to_tar(input_path, output_path)
		elif ext == "7z" and target_format == "zip":
			sevenz_to_zip(input_path, output_path)
		elif ext == "gz" and target_format == "zip":
			with tarfile.open(input_path, 'r') as tar:
				with zipfile.ZipFile(output_path, 'w') as zipf:
					for member in tar.getmembers():
						if member.isfile():
							file_data = tar.extractfile(member).read()
							zipf.writestr(member.name, file_data)
		elif ext == "gz" and target_format == "zip":
			with open(input_path, 'rb') as gz_file:
				with zipfile.ZipFile(output_path, 'w') as zipf:
					zipf.writestr(os.path.basename(input_path), gz_file.read())
		else:
			return {"error": "Unsupported archive conversion"}

	else:
		return {"error": "Unsupported conversion"}

	return output_filename

@app.route("/convert", methods=["POST"])
def convert():
	"""
	Convert uploaded files to the target format.
	"""
	files = request.files.getlist("files")
	target_format = request.form.get("target_format")
	if not files or not target_format:
		return jsonify({"error": "No files or target format specified"}), 400

	# Convert each file
	converted_files = []
	for file in files:
		if file:
			filename = secure_filename(file.filename)
			path = os.path.join(UPLOAD_FOLDER, filename)
			file.save(path)
			file_info = {"filename": filename, "target_format": target_format}
			result = convert_one(file_info)
			converted_files.append(result)

	# Zip converted files if more than one
	if len(converted_files) > 1:
		zip_filename = f"converted_{uuid.uuid4().hex}.zip"
		zip_path = os.path.join(CONVERTED_FOLDER, zip_filename)
		with zipfile.ZipFile(zip_path, 'w') as zipf:
			for converted_file in converted_files:
				file_path = os.path.join(CONVERTED_FOLDER, converted_file)
				zipf.write(file_path, arcname=converted_file)
		return send_file(zip_path, as_attachment=True)
	else:
		return send_file(os.path.join(CONVERTED_FOLDER, converted_files[0]), as_attachment=True)

priority = {
	"image": 0,
	"audio": 1,
	"video": 2,
	"document": 3,
	"archive": 4
}

# FIXME: Currently, only base formats w/ ZIP work
@app.route("/merge", methods=["POST"])
def merge():
	"""
	Merge uploaded files into a single polyglot file.
	"""
	files = request.files.getlist("files")
	if not files or len(files) < 2:
		return jsonify({"error": "At least two files are required for merging"}), 400

	base = uuid.uuid4().hex
	output_filename = f"{base}_merged"
	output_path = os.path.join(CONVERTED_FOLDER, output_filename)

	file_arr = []
	for file in files:
		if file:
			filename = secure_filename(file.filename)
			ext = os.path.splitext(filename)[1].lower()[1::]
			path = os.path.join(UPLOAD_FOLDER, filename)
			file.save(path)
			file_type = detect_type(ext)
			if file_type == "image":
				output_path = convert_one({"filename": filename, "target_format": "ico"})
			elif file_type == "audio" or file_type == "video":
				output_path = convert_one({"filename": filename, "target_format": "mp4"})
			elif file_type == "document":
				output_path = convert_one({"filename": filename, "target_format": "pdf"})
			elif file_type == "archive":
				output_path = convert_one({"filename": filename, "target_format": "zip"})

			if isinstance(output_path, dict):
				output_path = os.path.join(UPLOAD_FOLDER, output_path["filename"])
			else:
				output_path = os.path.join(CONVERTED_FOLDER, output_path)

			file_arr.append(output_path)

	# Merge all files into a single polyglot file
	file_arr = sorted(file_arr, key=lambda f: priority.get(detect_type(f.split('.')[-1].lower()), 5))
	base_file = None
	for file in file_arr:
		if detect_type(file.split('.')[-1].lower()) in ["audio", "video", "document"]:
			base_file = file
			break

	if not base_file:
		return jsonify({"error": "No suitable base file (audio/video/document)"}), 400

	# Create a polyglot file by appending files
	merged_path = os.path.join(CONVERTED_FOLDER, f"{uuid.uuid4().hex}_merged.polyglot")

	# Merge the files properly (use byte concatenation with open in binary mode)
	with open(merged_path, 'wb') as merged_file:
		with open(base_file, 'rb') as base_f:
			merged_file.write(base_f.read())

		for file in file_arr:
			if file != base_file:
				with open(file, 'rb') as f:
					merged_file.write(f.read())
	return send_file(merged_path, as_attachment=True, download_name=f"{output_filename}.polyglot")

def detect_type(ext):
	"""
	Detect the type of file based on its extension.
	Args:
		ext (str): The file extension.
	"""
	if ext in image_exts:
		return "image"
	elif ext in audio_exts:
		return "audio"
	elif ext in video_exts:
		return "video"
	elif ext in doc_exts:
		return "document"
	elif ext in archive_exts:
		return "archive"
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

	app.run(host="0.0.0.0", port=5000)