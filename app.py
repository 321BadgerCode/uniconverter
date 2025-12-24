import os
import sys
import uuid
import json
import struct
import zipfile
import tarfile
import tempfile
import binascii
import subprocess
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename

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

				allowed_sizes = [32, 64, 128]

				# Determine the target size based on the original image size
				max_dim = img.width
				target_size = max([s for s in allowed_sizes if s <= max_dim], default=min(allowed_sizes))
				img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)

				# Save the image as ICO
				img.save(output_path, format=target_format.upper(), sizes=[(target_size, target_size)])

			# Save the image in the target format
			if not target_format in ["ico", "svg", "heic"]:
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
			with open(input_path, "rb") as gz_file:
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

PRIORITY = {
	"video": 1,
	"image": 2,
	"document": 3,
	"archive": 4
}

PNG_SIG = b"\x89PNG\r\n\x1a\n"

def make_png_chunk(chunk_type, data):
	"""
	Create PNG chunk bytes: length(4) + type(4) + data + crc(4).
	Args:
		chunk_type (bytes): 4-byte chunk type (e.g., b'tEXt').
		data (bytes): Chunk data.
	"""
	assert len(chunk_type) == 4
	length = struct.pack(">I", len(data))
	crc = struct.pack(">I", binascii.crc32(chunk_type + data) & 0xffffffff)
	return length + chunk_type + data + crc

def insert_chunk_before_iend(png_bytes, chunk_type, chunk_data):
	"""
	Insert a chunk (type,data) immediately before the IEND chunk.
	Args:
		png_bytes (bytes): Original PNG bytes.
		chunk_type (bytes): 4-byte chunk type (e.g., b'tEXt').
		chunk_data (bytes): Chunk data to insert.
	"""
	# Look for the IEND chunk start: \x00\x00\x00\x00IEND
	marker = b"\x00\x00\x00\x00IEND"
	idx = png_bytes.rfind(marker)
	if idx == -1:
		# Fallback: look for "IEND" anywhere
		idx = png_bytes.rfind(b"IEND")
		if idx == -1:
			raise ValueError("IEND not found in PNG")
		# Find the 4 bytes length before it if possible
		idx = idx - 4
	chunk = make_png_chunk(chunk_type, chunk_data)
	return png_bytes[:idx] + chunk + png_bytes[idx:]

def find_png_inside_ico(ico_bytes):
	"""
	Return index of PNG signature inside ICO (or -1).
	Args:
		ico_bytes (bytes): ICO file bytes.
	"""
	return ico_bytes.find(PNG_SIG)

def append_mp4_box_bytes(base_bytes, box_type, payload, usertype = None):
	"""
	Append a valid top-level MP4 box with given box_type (4 bytes).
	If box_type == b'uuid' you must provide a 16-byte usertype (usertype).
	Args:
		base_bytes (bytes): Original MP4 bytes.
		box_type (bytes): 4-byte box type (e.g., b'uuid').
		payload (bytes): Payload data to append.
		usertype (bytes, optional): 16-byte usertype for 'uuid' box type. Defaults to None.
	"""
	if box_type == b'uuid':
		if usertype is None:
			usertype = uuid.uuid4().bytes # 16 bytes
		header_size = 8 + 16 # size(4) + type(4) + usertype(16)
		total_size = header_size + len(payload)
		return base_bytes + struct.pack(">I", total_size) + box_type + usertype + payload
	else:
		header_size = 8
		total_size = header_size + len(payload)
		return base_bytes + struct.pack(">I", total_size) + box_type + payload

def verify_mp4(path):
	"""
	Use ffprobe to quickly check basic mp4 readability.
	Args:
		path (str): Path to the MP4 file.
	"""
	try:
		cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
		res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
		return res.returncode == 0 and res.stdout.strip() != b""
	except Exception:
		return False

def extract_pdf_bytes(blob):
	"""
	Find a %PDF- ... %%EOF region inside blob and return bytes or None.
	Args:
		blob (bytes): The binary data to search for PDF content.
	"""
	start = blob.find(b"%PDF-")
	if start == -1:
		return None
	# Find last %%EOF after start
	eof_idx = blob.find(b"%%EOF", start)
	if eof_idx == -1:
		return None
	# Include %%EOF (and possibly following newline)
	end = eof_idx + len(b"%%EOF")
	# If there are multiple EOFs, prefer the last one after start:
	idx = blob.rfind(b"%%EOF")
	if idx >= start:
		end = idx + len(b"%%EOF")
	return blob[start:end]

def extract_png_bytes(blob):
	"""
	Find a PNG ... IEND region inside blob and return bytes or None.
	Args:
		blob (bytes): The binary data to search for PNG content.
	"""
	start = blob.find(PNG_SIG)
	if start == -1:
		return None
	# Find IEND
	iend_marker = b"\x00\x00\x00\x00IEND"
	iend_idx = blob.find(iend_marker, start)
	if iend_idx == -1:
		return None
	# Grab until IEND + CRC(4)
	end = iend_idx + 4 + 4 + 4 # Length(4)=0 + "IEND"(4) + CRC(4)
	# Safer: find the CRC 4 bytes after the IEND sequence
	return blob[start:end]

def merge_with_mp4_base(base_path, extras, merged_path):
	"""
	Merge extras into an MP4 base file by appending them as 'uuid' boxes.
	Args:
		base_path (str): MP4 path.
		extras (list): List of tuples (filename_on_disk, mime_type/extension).
		strategy (str): Append each extra as a uuid box (so mp4 players ignore it).
	"""
	with open(base_path, "rb") as f:
		base_bytes = f.read()

	for (extra_path, ext) in extras:
		with open(extra_path, "rb") as ef:
			payload = ef.read()
		# Use "uuid" box so the data is a valid top-level atom
		base_bytes = append_mp4_box_bytes(base_bytes, b"uuid", payload)

	with open(merged_path, "wb") as out:
		out.write(base_bytes)

	# Verify mp4 is still readable
	if not verify_mp4(merged_path):
		return False, "ffprobe failed; appended atoms probably broke the mp4 structure."

	return True, "OK"

def merge_with_png_base(base_path, extras, merged_path):
	"""
	Merge extras into a PNG base file by inserting them as ancillary chunks.
	Args:
		base_path (str): PNG path.
		extras (list): List of tuples (filename_on_disk, mime_type/extension).
		merged_path (str): Output path for the merged PNG.
	"""
	with open(base_path, "rb") as f:
		base_bytes = f.read()

	# If ICO with PNG inside, find PNG offset and replace that region
	if base_bytes[:8] != PNG_SIG and find_png_inside_ico(base_bytes) != -1:
		ico_bytes = base_bytes
		png_idx = find_png_inside_ico(ico_bytes)
		png_blob = ico_bytes[png_idx:]
		# Inject a chunk containing concatenated extras (we'll just concatenate extras)
		all_extra_payload = b""
		for (extra_path, ext) in extras:
			with open(extra_path, "rb") as ef:
				all_extra_payload += b"\n--EMBED--" + os.path.basename(extra_path).encode("utf-8") + b"\n" + ef.read()
		new_png = insert_chunk_before_iend(png_blob, b"pLTg", all_extra_payload)
		merged_bytes = ico_bytes[:png_idx] + new_png
		with open(merged_path, "wb") as out:
			out.write(merged_bytes)
		# Quick verify: PNG still has signature & IEND
		if new_png.startswith(PNG_SIG) and b"IEND" in new_png:
			return True, "OK"
		return False, "PNG injection failed"

def merge_with_pdf_base(base_path, extras, merged_path):
	"""
	Merge extras into a PDF base file by embedding them as attachments.
	Args:
		base_path (str): PDF path.
		extras (list): List of tuples (filename_on_disk, mime_type/extension).
		merged_path (str): Output path for the merged PDF.
	"""
	try:
		from pypdf import PdfReader, PdfWriter
		writer = PdfWriter(clone_from=base_path)
		for (extra_path, ext) in extras:
			with open(extra_path, "rb") as ef:
				data = ef.read()
			writer.add_attachment(filename=os.path.basename(extra_path), data=data)
		with open(merged_path, "wb") as out:
			writer.write(out)
		# Quick verify
		r = PdfReader(merged_path)
		# Attachments may be at r.attachments or accessed by names; ensure file opens
		return True, "OK"
	except Exception as e:
		return False, f"PDF embedding failed: {e}"

# FIXME: Fix merging of image files to work with video files
@app.route("/merge", methods=["POST"])
def merge():
	"""
	Merge multiple files into a single file based on their types.
	"""
	files = request.files.getlist("files")
	if not files or len(files) < 2:
		return jsonify({"error": "At least two files are required for merging"}), 400

	converted = []
	# Convert each incoming file to the target format
	for f in files:
		filename = secure_filename(f.filename)
		ext = os.path.splitext(filename)[1].lower().lstrip(".")
		save_path = os.path.join(UPLOAD_FOLDER, filename)
		f.save(save_path)

		file_type = detect_type(ext)
		target = None
		if file_type == "image":
			target = "ico"
		elif file_type in ("audio", "video"):
			target = "mp4"
		elif file_type == "document":
			target = "pdf"
		elif file_type == "archive":
			target = "zip"

		if target:
			conv = convert_one({"filename": filename, "target_format": target})
			if isinstance(conv, dict):
				conv_path = os.path.join(UPLOAD_FOLDER, conv["filename"])
			else:
				conv_path = os.path.join(CONVERTED_FOLDER, conv)
		else:
			conv_path = save_path
		converted.append(conv_path)

	# Choose base using priority (lowest number = highest priority)
	converted = sorted(converted, key=lambda p: PRIORITY.get(detect_type(os.path.splitext(p)[1].lower().lstrip(".")), 5))
	base = converted[0]
	extras = [(p, os.path.splitext(p)[1].lower().lstrip(".")) for p in converted[1:]]

	outname = f"{uuid.uuid4().hex}_merged.polyglot"
	outpath = os.path.join(CONVERTED_FOLDER, outname)

	base_ext = os.path.splitext(base)[1].lower().lstrip(".")

	# Use strategy depending on base_ext
	if base_ext == "mp4":
		ok, msg = merge_with_mp4_base(base, extras, outpath)
	elif base_ext == "ico":
		ok, msg = merge_with_png_base(base, extras, outpath)
	elif base_ext == "pdf":
		ok, msg = merge_with_pdf_base(base, extras, outpath)
	else:
		# Fallback: append raw bytes with byte concatenation and try to verify via magic scanning
		with open(base, "rb") as bf:
			out_bytes = bf.read()
		for p, ext in extras:
			with open(p, "rb") as ef:
				out_bytes += ef.read()
		with open(outpath, "wb") as out:
			out.write(out_bytes)
		ok = True
		msg = "Appended raw bytes (fallback)."

	if not ok:
		return jsonify({"error": "merge failed", "detail": msg}), 500

	return send_file(outpath, as_attachment=True, download_name=outname)

@app.route("/metadata", methods=["POST"])
def get_metadata():
	"""
	Get metadata for a file using ExifTool.
	Args:
		filepath (str): Path to the file for which metadata is requested.
	"""
	data = request.get_json()
	if not data or "filepath" not in data:
		return jsonify({"error": "Missing \"filepath\" in JSON"}), 400

	filepath = os.path.join(UPLOAD_FOLDER, secure_filename(data["filepath"]))
	if not os.path.exists(filepath):
		return jsonify({"error": "File does not exist"}), 404

	try:
		result = subprocess.run(
			["exiftool", "-j", filepath],
			capture_output=True,
			text=True,
			check=True
		)
		metadata = json.loads(result.stdout)[0]
		return jsonify(metadata)

	except subprocess.CalledProcessError as e:
		return jsonify({"error": "Exiftool command failed", "details": e.stderr}), 500
	except (json.JSONDecodeError, IndexError):
		return jsonify({"error": "Failed to parse exiftool output"}), 500

@app.route("/metadata/delete", methods=["POST"])
def delete_metadata():
	"""
	Delete metadata for a file using ExifTool.
	Args:
		filepath (str): Path to the file for which metadata is requested.
	"""
	data = request.get_json()
	if not data or "filepath" not in data:
		return jsonify({"error": "Missing \"filepath\" in JSON"}), 400

	filepath = os.path.join(UPLOAD_FOLDER, secure_filename(data["filepath"]))
	if not os.path.exists(filepath):
		return jsonify({"error": "File does not exist"}), 404

	try:
		result = subprocess.run(
			["exiftool", "-all=", filepath],
			capture_output=True,
			text=True,
			check=True
		)
		return jsonify({"success": True})

	except subprocess.CalledProcessError as e:
		return jsonify({"error": "Exiftool command failed", "details": e.stderr}), 500

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