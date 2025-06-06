import os
import argparse
import subprocess
import uuid
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
CONVERTED_FOLDER = "converted"

image_exts = ["jpg", "jpeg", "png", "webp", "gif"]
audio_exts = ["mp3", "wav", "flac", "aac", "m4a", "ogg"]
video_exts = ["mp4", "mov", "avi", "webm"]
doc_exts = ["pdf", "txt"]

is_backup_enabled = False

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CONVERTED_FOLDER, exist_ok=True)

@app.route('/')
def index():
	return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
	file = request.files["file"]
	if file:
		filename = secure_filename(file.filename)
		ext = os.path.splitext(filename)[1].lower()[1::]
		path = os.path.join(UPLOAD_FOLDER, filename)
		file.save(path)

		file_type = detect_type(ext)
		return {"filename": filename, "type": file_type}
	return {"error": "No file uploaded"}

@app.route("/convert", methods=["POST"])
def convert():
	data = request.form
	filename = data["filename"]
	target_format = data["target_format"]
	input_path = os.path.join(UPLOAD_FOLDER, filename)
	ext = os.path.splitext(filename)[1].lower()[1::]
	base = uuid.uuid4().hex
	output_filename = f"{base}.{target_format}"
	output_path = os.path.join(CONVERTED_FOLDER, output_filename)

	if ext in image_exts and target_format in image_exts:
		try:
			from PIL import Image
			img = Image.open(input_path).convert("RGB")
			if target_format in ["jpg", "jpeg"]:
				img = img.convert("RGB")
				target_format = "jpeg"
			img.save(output_path, target_format.upper())
		except ImportError as e:
			print("Pillow error: PIL module not found")
			print("Error details:", e)
			return jsonify({"error": "Pillow is required for image conversion."}), 500

	elif ext in audio_exts and target_format in audio_exts:
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

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="File Converter Service")
	parser.add_argument("--cleanup", action="store_true", help="Delete uploaded and converted files on exit")
	args = parser.parse_args()

	app.run(debug=True)

	if args.cleanup:
		folders = [UPLOAD_FOLDER, CONVERTED_FOLDER]
		for folder in folders:
			for filename in os.listdir(folder):
				file_path = os.path.join(folder, filename)
				try:
					os.remove(file_path)
				except Exception as e:
					print(f"Error deleting {file_path}: {e}")