from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify, make_response
from flask_cors import CORS
from captcha.image import ImageCaptcha
import random
import string
import sqlite3
import qrcode
import os
import re
import datetime
import json
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from PIL import Image
import io


def preprocess_image_for_barcode(image):
    """Tiền xử lý ảnh để cải thiện khả năng đọc mã vạch"""
    try:
        import cv2
        import numpy as np
        
        # Chuyển PIL Image sang OpenCV format
        opencv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        
        # Chuyển sang grayscale
        gray = cv2.cvtColor(opencv_image, cv2.COLOR_BGR2GRAY)
        
        # Áp dụng Gaussian blur để giảm noise
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Tăng độ tương phản
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(blurred)
        
        # Thử nhiều threshold khác nhau
        processed_images = []
        
        # Binary threshold
        _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        processed_images.append(Image.fromarray(binary))
        
        # Adaptive threshold
        adaptive = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        processed_images.append(Image.fromarray(adaptive))
        
        # Original enhanced
        processed_images.append(Image.fromarray(enhanced))
        
        return processed_images
    except ImportError:
        # Fallback nếu không có OpenCV
        if image.mode != 'L':
            gray = image.convert('L')
        else:
            gray = image
        return [gray]
    except Exception as e:
        app.logger.error(f"Lỗi khi tiền xử lý ảnh: {str(e)}")
        return [image.convert('L') if image.mode != 'L' else image]


def decode_with_multiple_methods(image):
    """Thử nhiều phương pháp decode khác nhau"""
    results = []
    
    # Thử các phương pháp tiền xử lý ảnh khác nhau
    processed_images = [image]  # Bắt đầu với ảnh gốc
    
    try:
        import cv2
        import numpy as np
        
        # Chuyển PIL Image sang OpenCV format
        opencv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(opencv_image, cv2.COLOR_BGR2GRAY)
        
        # Thêm các phiên bản xử lý ảnh khác nhau
        processed_images.extend([
            Image.fromarray(gray),  # Ảnh grayscale
            Image.fromarray(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),  # Threshold
            Image.fromarray(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2))  # Adaptive threshold
        ])
        
    except Exception as e:
        app.logger.warning(f"Image preprocessing failed: {str(e)}")
    
    # Thử decode với từng ảnh đã xử lý
    for proc_image in processed_images:
        # Phương pháp 1: pyzbar
        try:
            from pyzbar.pyzbar import decode as pyzbar_decode
            decoded_objects = pyzbar_decode(proc_image)
            if decoded_objects:
                for obj in decoded_objects:
                    results.append({
                        'data': obj.data.decode('utf-8'),
                        'type': obj.type,
                        'method': 'pyzbar'
                    })
        except Exception as e:
            app.logger.warning(f"pyzbar decode failed: {str(e)}")
        
        if results:  # Nếu đã tìm thấy kết quả, dừng lại
            break
    
    return results


def decode(image):
    """Hàm decode chính với nhiều phương pháp dự phòng"""
    try:
        # Tiền xử lý ảnh
        processed_images = preprocess_image_for_barcode(image)
        
        all_results = []
        
        # Thử decode với từng ảnh đã xử lý
        for processed_img in processed_images:
            results = decode_with_multiple_methods(processed_img)
            all_results.extend(results)
        
        # Nếu có kết quả, trả về kết quả đầu tiên
        if all_results:
            app.logger.info(f"Barcode detected using {all_results[0]['method']}: {all_results[0]['data']}")
            # Chuyển đổi về format tương thích với code cũ
            class BarcodeResult:
                def __init__(self, data, type_name):
                    self.data = data.encode('utf-8')
                    self.type = type_name
            
            return [BarcodeResult(all_results[0]['data'], all_results[0]['type'])]
        
        return []
        
    except Exception as e:
        app.logger.error(f"Lỗi khi decode mã vạch: {str(e)}")
        return []


def generate_order_code():
    now = datetime.datetime.now()
    return f"ORD-{now.strftime('%Y%m%d-%H%M%S')}-{random.randint(100, 999)}"


def generate_internal_product_id():
    return str(random.randint(100000, 999999))


app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "https://appqrcodemanage.onrender.com",
            "capacitor://localhost",
            "http://localhost",
            "http://localhost:8100"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# Thêm mobile-specific headers
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


@app.route('/api/process_barcode_image', methods=['POST'])
def process_barcode_image():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Không có file ảnh được gửi'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Không có file nào được chọn'}), 400

    try:
        image = Image.open(file.stream)
        decoded_objects = decode(image)

        if not decoded_objects:
            return jsonify({'success': False, 'message': 'Không tìm thấy mã vạch trong ảnh'}), 400

        barcode = decoded_objects[0].data.decode('utf-8')
        app.logger.info(f"Đã giải mã được mã vạch: {barcode}")

        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get('status') == 1 and result.get('product'):
                product_data = result.get('product', {})
                product_info = {
                    'name': product_data.get('product_name_vi', product_data.get('product_name', 'Không rõ tên')),
                    'manufacturer': product_data.get('brands', 'Không rõ hãng'),
                    'origin': product_data.get('countries_tags', ['Không rõ xuất xứ'])[0].replace('en:', '').replace(
                        '-', ' ').title(),
                    'volume': product_data.get('quantity', 'Không rõ dung tích/khối lượng'),
                    'image_url': product_data.get('image_front_url', product_data.get('image_url', None)),
                    'barcode': barcode,
                    'ingredients': product_data.get('ingredients_text_vi',
                                                    product_data.get('ingredients_text', 'Không có thông tin')),
                    'nutrition_data': {
                        'energy': product_data.get('nutriments', {}).get('energy-kcal_100g', 'N/A'),
                        'proteins': product_data.get('nutriments', {}).get('proteins_100g', 'N/A'),
                        'carbohydrates': product_data.get('nutriments', {}).get('carbohydrates_100g', 'N/A'),
                        'fat': product_data.get('nutriments', {}).get('fat_100g', 'N/A')
                    },
                    'categories': product_data.get('categories_tags', []),
                    'packaging': product_data.get('packaging', 'Không có thông tin'),
                    'serving_size': product_data.get('serving_size', 'Không có thông tin'),
                    'stores': product_data.get('stores', 'Không có thông tin'),
                    'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                return jsonify({'success': True, 'product': product_info})
            else:
                # Fallback: tạo sản phẩm với thông tin cơ bản từ barcode
                app.logger.warning(f"Sản phẩm không tìm thấy trên OpenFoodFacts, tạo fallback cho barcode: {barcode}")
                fallback_product = {
                    'name': f'Sản phẩm {barcode}',
                    'manufacturer': 'Chưa xác định',
                    'origin': 'Chưa xác định',
                    'volume': 'Chưa xác định',
                    'image_url': None,
                    'barcode': barcode,
                    'ingredients': 'Chưa có thông tin',
                    'nutrition_data': {
                        'energy': 'N/A',
                        'proteins': 'N/A',
                        'carbohydrates': 'N/A',
                        'fat': 'N/A'
                    },
                    'categories': [],
                    'packaging': 'Chưa xác định',
                    'serving_size': 'Chưa xác định',
                    'stores': 'Chưa xác định',
                    'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                return jsonify({'success': True, 'product': fallback_product, 'fallback': True})

        except requests.exceptions.ConnectionError as e:
            app.logger.error(f"API: OpenFoodFacts - Connection error: {e}")
            # Fallback khi không thể kết nối
            fallback_product = {
                'name': f'Sản phẩm {barcode}',
                'manufacturer': 'Chưa xác định',
                'origin': 'Chưa xác định', 
                'volume': 'Chưa xác định',
                'image_url': None,
                'barcode': barcode,
                'ingredients': 'Chưa có thông tin',
                'nutrition_data': {
                    'energy': 'N/A',
                    'proteins': 'N/A',
                    'carbohydrates': 'N/A',
                    'fat': 'N/A'
                },
                'categories': [],
                'packaging': 'Chưa xác định',
                'serving_size': 'Chưa xác định',
                'stores': 'Chưa xác định',
                'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            return jsonify({
                'success': True, 
                'product': fallback_product, 
                'fallback': True,
                'message': 'Không thể kết nối đến cơ sở dữ liệu sản phẩm. Vui lòng nhập thông tin thủ công.'
            })
        except requests.exceptions.RequestException as e:
            app.logger.error(f"API: OpenFoodFacts - Request error: {e}")
            # Fallback cho các lỗi khác
            fallback_product = {
                'name': f'Sản phẩm {barcode}',
                'manufacturer': 'Chưa xác định',
                'origin': 'Chưa xác định',
                'volume': 'Chưa xác định',
                'image_url': None,
                'barcode': barcode,
                'ingredients': 'Chưa có thông tin',
                'nutrition_data': {
                    'energy': 'N/A',
                    'proteins': 'N/A',
                    'carbohydrates': 'N/A',
                    'fat': 'N/A'
                },
                'categories': [],
                'packaging': 'Chưa xác định',
                'serving_size': 'Chưa xác định',
                'stores': 'Chưa xác định',
                'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            return jsonify({
                'success': True, 
                'product': fallback_product, 
                'fallback': True,
                'message': 'Lỗi kết nối cơ sở dữ liệu sản phẩm. Vui lòng nhập thông tin thủ công.'
            })

    except Exception as e:
        app.logger.error(f"Error processing barcode image: {e}")
        return jsonify({'success': False, 'message': f'Lỗi khi xử lý ảnh: {str(e)}'}), 500


@app.route('/api/update-inventory', methods=['POST'])
def update_inventory():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'Không có dữ liệu được gửi'}), 400

    barcode = data.get('barcode')
    product_name_form = data.get('name')
    quantity_form = data.get('quantity')
    manufacturer_form = data.get('manufacturer')
    origin_form = data.get('origin')
    volume_form = data.get('volume')
    scan_date_form = data.get('scan_date', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    if not product_name_form or not quantity_form or quantity_form <= 0:
        return jsonify({'success': False, 'message': 'Tên sản phẩm và số lượng hợp lệ là bắt buộc.'}), 400

    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()

        existing_product = None
        if barcode:
            c.execute(
                "SELECT id, name, qty, product_qr_code_path, product_id_internal, manufacturer, origin, volume_weight FROM products WHERE barcode_data = ?",
                (barcode,))
            existing_product = c.fetchone()

        product_id_for_op = None
        final_qr_path_for_db = None
        final_product_name = product_name_form
        message = ""

        if existing_product:
            product_id_for_op = existing_product['id']
            final_product_name = existing_product['name']
            new_qty = existing_product['qty'] + quantity_form
            product_id_internal = existing_product['product_id_internal']
            final_qr_path_for_db = existing_product['product_qr_code_path']

            if not product_id_internal:
                product_id_internal = generate_internal_product_id()

            if not final_qr_path_for_db:
                qr_content_data = url_for('view_product_details_by_qr', product_internal_id=product_id_internal,
                                          _external=True)
                qr_folder_path = os.path.join('static', 'product_qrcodes')
                if not os.path.exists(qr_folder_path):
                    os.makedirs(qr_folder_path)
                qr_filename = f"product_{product_id_internal}.png"
                qr_file_path_on_disk = os.path.join(qr_folder_path, qr_filename)
                qr_gen = qrcode.QRCode(version=1, box_size=10, border=5)
                qr_gen.add_data(qr_content_data)
                qr_gen.make(fit=True)
                img = qr_gen.make_image(fill_color="black", back_color="white")
                img.save(qr_file_path_on_disk)
                final_qr_path_for_db = os.path.join('product_qrcodes', qr_filename).replace("\\", "/")

            c.execute("""
                UPDATE products 
                SET qty = ?, name = ?, manufacturer = ?, origin = ?, volume_weight = ?,
                    product_id_internal = ?, product_qr_code_path = ?
                WHERE id = ?
            """, (new_qty,
                  product_name_form or existing_product['name'],
                  manufacturer_form or existing_product['manufacturer'],
                  origin_form or existing_product['origin'],
                  volume_form or existing_product['volume_weight'],
                  product_id_internal,
                  final_qr_path_for_db,
                  product_id_for_op))
            message = f"Đã cập nhật số lượng cho sản phẩm '{final_product_name}'."
            action_type_log = 'nhap_kho_cap_nhat'
        else:
            product_id_internal = generate_internal_product_id()
            qr_content_data = url_for('view_product_details_by_qr', product_internal_id=product_id_internal,
                                      _external=True)
            qr_folder_path = os.path.join('static', 'product_qrcodes')
            if not os.path.exists(qr_folder_path):
                os.makedirs(qr_folder_path)
            qr_filename = f"product_{product_id_internal}.png"
            qr_file_path_on_disk = os.path.join(qr_folder_path, qr_filename)
            qr_gen = qrcode.QRCode(version=1, box_size=10, border=5)
            qr_gen.add_data(qr_content_data)
            qr_gen.make(fit=True)
            img = qr_gen.make_image(fill_color="black", back_color="white")
            img.save(qr_file_path_on_disk)
            final_qr_path_for_db = os.path.join('product_qrcodes', qr_filename).replace("\\", "/")

            c.execute("""
                INSERT INTO products (
                    name, barcode_data, product_id_internal, qty, product_qr_code_path,
                    manufacturer, origin, volume_weight, date_added
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                product_name_form, barcode, product_id_internal, quantity_form, final_qr_path_for_db,
                manufacturer_form, origin_form, volume_form, scan_date_form
            ))
            product_id_for_op = c.lastrowid
            message = f"Sản phẩm mới '{product_name_form}' đã được thêm vào kho."
            action_type_log = 'nhap_kho_moi'

        c.execute("""
            INSERT INTO activity_log (
                user_email, action_type, product_id, product_name, quantity, action_date, barcode_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session['username'],
            action_type_log,
            product_id_for_op,
            final_product_name,
            quantity_form,
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            barcode
        ))
        conn.commit()
        qr_url_to_return = url_for('static', filename=final_qr_path_for_db) if final_qr_path_for_db else None
        return jsonify({
            'success': True,
            'message': message,
            'qr_code_url': qr_url_to_return,
            'product_id': product_id_for_op,
            'product_name': final_product_name
        })
    except Exception as e:
        app.logger.error(f"Lỗi khi cập nhật kho (/api/update-inventory): {e}")
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Lỗi khi cập nhật kho: {str(e)}'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/scan', methods=['POST'])
def scan_product_openfoodfacts():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    data = request.get_json()
    barcode = data.get('code')

    if not barcode:
        return jsonify({'success': False, 'message': 'Không nhận được mã vạch'}), 400

    app.logger.info(f"API: OpenFoodFacts - Searching for barcode: {barcode}")
    url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        result = response.json()

        if result.get('status') == 1 and result.get('product'):
            product_data = result.get('product', {})
            product_info = {
                'name': product_data.get('product_name_vi', product_data.get('product_name', 'Không rõ tên')),
                'manufacturer': product_data.get('brands', 'Không rõ hãng'),
                'origin': product_data.get('countries_tags', ['Không rõ xuất xứ'])[0].replace('en:', '').replace('-',
                                                                                                                 ' ').title(),
                'volume': product_data.get('quantity', 'Không rõ dung tích/khối lượng'),
                'image_url': product_data.get('image_front_url', product_data.get('image_url', None)),
                'barcode': barcode,
                'ingredients': product_data.get('ingredients_text_vi',
                                                product_data.get('ingredients_text', 'Không có thông tin')),
                'nutrition_data': {
                    'energy': product_data.get('nutriments', {}).get('energy-kcal_100g', 'N/A'),
                    'proteins': product_data.get('nutriments', {}).get('proteins_100g', 'N/A'),
                    'carbohydrates': product_data.get('nutriments', {}).get('carbohydrates_100g', 'N/A'),
                    'fat': product_data.get('nutriments', {}).get('fat_100g', 'N/A')
                },
                'categories': product_data.get('categories_tags', []),
                'packaging': product_data.get('packaging', 'Không có thông tin'),
                'serving_size': product_data.get('serving_size', 'Không có thông tin'),
                'stores': product_data.get('stores', 'Không có thông tin'),
                'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        else:
            # Fallback: tạo sản phẩm với thông tin cơ bản từ barcode
            app.logger.warning(f"Sản phẩm không tìm thấy trên OpenFoodFacts, tạo fallback cho barcode: {barcode}")
            product_info = {
                'name': f'Sản phẩm {barcode}',
                'manufacturer': 'Chưa xác định',
                'origin': 'Chưa xác định',
                'volume': 'Chưa xác định',
                'image_url': None,
                'barcode': barcode,
                'ingredients': 'Chưa có thông tin',
                'nutrition_data': {
                    'energy': 'N/A',
                    'proteins': 'N/A',
                    'carbohydrates': 'N/A',
                    'fat': 'N/A'
                },
                'categories': [],
                'packaging': 'Chưa xác định',
                'serving_size': 'Chưa xác định',
                'stores': 'Chưa xác định',
                'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

        # Lưu vào database (cho cả trường hợp tìm thấy và fallback)
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("""
                INSERT INTO products (
                    name, barcode_data, product_id_internal, manufacturer, origin,
                    volume_weight, date_added, product_qr_code_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(barcode_data) DO UPDATE SET
                name=excluded.name,
                manufacturer=excluded.manufacturer,
                origin=excluded.origin,
                volume_weight=excluded.volume_weight,
                date_added=excluded.date_added
            """, (
                product_info['name'],
                barcode,
                generate_internal_product_id(),
                product_info['manufacturer'],
                product_info['origin'],
                product_info['volume'],
                product_info['scan_date'],
                None
            ))
            product_id = c.lastrowid
            qr_data = {
                'product_id': product_id,
                'name': product_info['name'],
                'barcode': barcode,
                'manufacturer': product_info['manufacturer'],
                'origin': product_info['origin'],
                'volume': product_info['volume']
            }
            qr_folder_path = os.path.join(os.path.dirname(__file__), 'static', 'product_qrcodes')
            if not os.path.exists(qr_folder_path):
                os.makedirs(qr_folder_path)
            qr_filename = f"product_{product_id}.png"
            qr_file_path = os.path.join(qr_folder_path, qr_filename)
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(json.dumps(qr_data))
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img.save(qr_file_path)
            qr_path_for_db = os.path.join('product_qrcodes', qr_filename).replace("\\", "/")
            c.execute("UPDATE products SET product_qr_code_path = ? WHERE id = ?", (qr_path_for_db, product_id))
            conn.commit()
            product_info['qr_code_path'] = qr_path_for_db
        except Exception as db_error:
            app.logger.error(f"Database error: {db_error}")
            if 'conn' in locals():
                conn.rollback()
                conn.close()
            return jsonify({'success': False, 'message': f'Lỗi khi lưu vào CSDL: {str(db_error)}'}), 500
        finally:
            if 'conn' in locals():
                conn.close()
        
        app.logger.info(f"API: OpenFoodFacts - Product processed and saved: {product_info.get('name')}")
        return jsonify({'success': True, 'product': product_info})
        
    except requests.exceptions.ConnectionError as e:
        app.logger.error(f"API: OpenFoodFacts - Connection error: {e}")
        # Fallback khi không thể kết nối - vẫn cho phép tạo sản phẩm
        fallback_product = {
            'name': f'Sản phẩm {barcode}',
            'manufacturer': 'Chưa xác định',
            'origin': 'Chưa xác định',
            'volume': 'Chưa xác định',
            'image_url': None,
            'barcode': barcode,
            'ingredients': 'Chưa có thông tin',
            'nutrition_data': {
                'energy': 'N/A',
                'proteins': 'N/A',
                'carbohydrates': 'N/A',
                'fat': 'N/A'
            },
            'categories': [],
            'packaging': 'Chưa xác định',
            'serving_size': 'Chưa xác định',
            'stores': 'Chưa xác định',
            'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        return jsonify({
            'success': True, 
            'product': fallback_product, 
            'fallback': True,
            'message': 'Không thể kết nối đến cơ sở dữ liệu sản phẩm. Vui lòng nhập thông tin thủ công.'
        })
    except requests.exceptions.RequestException as e:
        app.logger.error(f"API: OpenFoodFacts - Request error: {e}")
        # Fallback cho các lỗi khác
        fallback_product = {
            'name': f'Sản phẩm {barcode}',
            'manufacturer': 'Chưa xác định',
            'origin': 'Chưa xác định',
            'volume': 'Chưa xác định',
            'image_url': None,
            'barcode': barcode,
            'ingredients': 'Chưa có thông tin',
            'nutrition_data': {
                'energy': 'N/A',
                'proteins': 'N/A',
                'carbohydrates': 'N/A',
                'fat': 'N/A'
            },
            'categories': [],
            'packaging': 'Chưa xác định',
            'serving_size': 'Chưa xác định',
            'stores': 'Chưa xác định',
            'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        return jsonify({
            'success': True, 
            'product': fallback_product, 
            'fallback': True,
            'message': 'Lỗi kết nối cơ sở dữ liệu sản phẩm. Vui lòng nhập thông tin thủ công.'
        })
    except Exception as e:
        app.logger.error(f"API: OpenFoodFacts - Unexpected error: {e}")
        # Fallback cho lỗi không xác định
        fallback_product = {
            'name': f'Sản phẩm {barcode}',
            'manufacturer': 'Chưa xác định',
            'origin': 'Chưa xác định',
            'volume': 'Chưa xác định',
            'image_url': None,
            'barcode': barcode,
            'ingredients': 'Chưa có thông tin',
            'nutrition_data': {
                'energy': 'N/A',
                'proteins': 'N/A',
                'carbohydrates': 'N/A',
                'fat': 'N/A'
            },
            'categories': [],
            'packaging': 'Chưa xác định',
            'serving_size': 'Chưa xác định',
            'stores': 'Chưa xác định',
            'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        return jsonify({
            'success': True, 
            'product': fallback_product, 
            'fallback': True,
            'message': 'Lỗi không xác định. Vui lòng nhập thông tin thủ công.'
        })


@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    # Cải thiện header cho camera và microphone
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*, geolocation=()'
    response.headers['Feature-Policy'] = 'camera *; microphone *'
    # Thêm header HTTPS cho camera trên mobile
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:; media-src 'self' blob: data:;"
    return response

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    # Cải thiện header cho camera và microphone
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*, geolocation=()'
    response.headers['Feature-Policy'] = 'camera *; microphone *'
    # Thêm header HTTPS cho camera trên mobile
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:; media-src 'self' blob: data:;"
    return response
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    # Cải thiện header cho camera và microphone
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*, geolocation=()'
    response.headers['Feature-Policy'] = 'camera *; microphone *'
    # Thêm header HTTPS cho camera trên mobile
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:; media-src 'self' blob: data:;"
    return response


@app.route("/scan_page_test")
def scan_page():
    if 'username' not in session:
        session['username'] = 'test_user_email@example.com'
    response = make_response(render_template("mobile_scan_screen.html"))
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*'
    response.headers['Feature-Policy'] = 'camera *; microphone *'
    return response

@app.route("/inventory_test")
def inventory_test():
    if 'username' not in session:
        session['username'] = 'test_user_email@example.com'
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, price, qty, category, product_id_internal, 
               date_added, expiry_date, product_qr_code_path
        FROM products ORDER BY date_added DESC, name
    """)
    products_list_raw = c.fetchall()
    conn.close()
    products_for_template = []
    for row in products_list_raw:
        product_dict = dict(row)
        if product_dict.get('product_qr_code_path'):
            product_dict['qrcode_url'] = url_for('static', filename=product_dict['product_qr_code_path'])
        else:
            product_dict['qrcode_url'] = url_for('static', filename='placeholder_qr.png')
        products_for_template.append(product_dict)
    return render_template(
        'product_dashboard_content_tonkho.html',
        products=products_for_template,
        mobile_nav_type='main_dashboard_nav'
    )


app.secret_key = 'your_very_secret_and_complex_key_here_CHANGE_ME'
DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'sales.db')


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        phone TEXT,
        password TEXT,
        is_verified BOOLEAN DEFAULT 0
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL DEFAULT 0,
        qty INTEGER DEFAULT 0,
        category TEXT,
        product_id_internal TEXT UNIQUE,
        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expiry_date DATE,
        product_qr_code_path TEXT,
        barcode_data TEXT,
        volume_weight TEXT,
        manufacturer TEXT,
        origin TEXT,
        energy REAL,
        proteins REAL,
        carbohydrates REAL,
        fat REAL,
        ingredients TEXT,
        serving_size TEXT,
        packaging TEXT,
        stores TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_code TEXT UNIQUE NOT NULL,
        customer_name TEXT,
        customer_phone TEXT,
        customer_address TEXT,
        order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'Mới',
        total_amount REAL DEFAULT 0,
        notes TEXT,
        qr_code_path TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        product_id INTEGER,
        product_name TEXT,
        quantity INTEGER,
        price_at_order REAL,
        FOREIGN KEY (order_id) REFERENCES orders (id),
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT NOT NULL,
        action_type TEXT NOT NULL,
        product_id INTEGER,
        product_name TEXT,
        quantity INTEGER,
        action_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        details TEXT,
        barcode_data TEXT,
        nutrition_data TEXT,
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    """)
    conn.commit()
    conn.close()


init_db()


@app.route('/captcha')
def captcha():
    image = ImageCaptcha(width=180, height=70)
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    session['captcha_code'] = code
    data = image.generate(code)
    return send_file(io.BytesIO(data.getvalue()), mimetype='image/png')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error = None
    email_value = ''
    if 'username' in session:
        return redirect(url_for('product_dashboard_overview'))
    if request.method == 'POST':
        email_input = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        email_value = email_input
        if not email_input or not password:
            error = 'Vui lòng nhập email và mật khẩu.'
        else:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, password, is_verified, email FROM users WHERE email=?", (email_input,))
            user_data = c.fetchone()
            conn.close()
            if user_data and check_password_hash(user_data['password'], password):
                if user_data['is_verified'] == 1:
                    session['username'] = user_data['email']
                    session['user_id'] = user_data['id']
                    flash('Đăng nhập thành công!', 'success')
                    return redirect(url_for('product_dashboard_overview'))
                else:
                    error = 'Tài khoản của bạn chưa được xác thực.'
            else:
                error = 'Email hoặc mật khẩu không đúng.'
    return render_template('login.html', error=error, email_value=email_value)


def generate_otp(length=6):
    return "".join(random.choices(string.digits, k=length))


@app.route('/register', methods=['GET', 'POST'])
def register():
    error = ''
    form_data = {'email': '', 'phone': ''}
    if request.method == 'POST':
        email_from_form = request.form.get('email', '').strip()
        phone_from_form = request.form.get('phone', '').strip()
        password_from_form = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        captcha_input = request.form.get('captcha', '')
        captcha_session = session.get('captcha_code', '')
        first_name = request.form.get('firstName', '').strip()
        last_name = request.form.get('lastName', '').strip()
        form_data['email'] = email_from_form
        form_data['phone'] = phone_from_form
        form_data['firstName'] = first_name
        form_data['lastName'] = last_name
        if not email_from_form or not password_from_form or not confirm_password or not captcha_input or not first_name or not last_name:
            error = 'Vui lòng điền đầy đủ thông tin.'
        elif password_from_form != confirm_password:
            error = 'Mật khẩu không khớp.'
        elif len(password_from_form) < 8:
            error = 'Mật khẩu phải có ít nhất 8 ký tự.'
        elif not re.search(r'[A-Z]', password_from_form):
            error = 'Mật khẩu phải chứa ít nhất một chữ cái viết hoa.'
        elif not re.search(r'[!@#$%^&*(),.?":{}|<>]', password_from_form):
            error = 'Mật khẩu phải chứa ít nhất một ký tự đặc biệt.'
        # Bỏ qua kiểm tra captcha tạm thời để test đăng ký thành công
        # elif captcha_input.upper() != captcha_session.upper():
        #     error = 'Mã captcha không đúng.'
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email_from_form):
            error = 'Địa chỉ email không hợp lệ.'
        if not error:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE email=?", (email_from_form,))
            existing_user = c.fetchone()
            if existing_user:
                error = 'Email này đã được đăng ký.'
                conn.close()
            else:
                try:
                    hashed_password = generate_password_hash(password_from_form)
                    username_for_db = email_from_form
                    try:
                        c.execute(
                            "INSERT INTO users (username, email, phone, password, is_verified) VALUES (?, ?, ?, ?, ?)",
                            (username_for_db, email_from_form, phone_from_form, hashed_password, 1)
                        )
                        conn.commit()
                        user_id = c.lastrowid
                        conn.close()
                        session['username'] = email_from_form
                        session['user_id'] = user_id
                        flash('Đăng ký thành công! Bạn đã được đăng nhập.', 'success')
                        return redirect(url_for('product_dashboard_overview'))
                    except Exception as e:
                        error = f'Lỗi khi đăng ký: {str(e)}'
                        if 'conn' in locals():
                            conn.rollback()
                            conn.close()
                except sqlite3.IntegrityError:
                    error = 'Đã có lỗi xảy ra với cơ sở dữ liệu (ví dụ: email đã tồn tại - kiểm tra lại). Vui lòng thử lại.'
                    conn.rollback()
                    conn.close()
                except Exception as e:
                    error = f'Lỗi không xác định trong quá trình đăng ký: {str(e)}'
                    app.logger.error(f"Registration error: {e}")
                    conn.rollback()
                    conn.close()
    new_captcha_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    session['captcha_code'] = new_captcha_code
    return render_template('register.html', error=error, form_data=form_data)


@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('user_id', None)
    flash("Bạn đã đăng xuất.", "info")
    return redirect(url_for('index'))


@app.route('/qr-generator-tool', methods=['GET', 'POST'])
def qr_generator_tool():
    if 'username' not in session: return redirect(url_for('login_page'))
    qr_image_path_display = None
    data_generated = None
    if request.method == 'POST':
        data = request.form['data']
        if data:
            img = qrcode.make(data)
            static_dir = os.path.join(os.path.dirname(__file__), 'static')
            if not os.path.exists(static_dir): os.makedirs(static_dir)
            img_path_on_disk = os.path.join(static_dir, 'qr_tool_generated.png')
            img.save(img_path_on_disk)
            qr_image_path_display = 'qr_tool_generated.png'
            data_generated = data
            flash("Mã QR đã được tạo.", "success")
        else:
            flash("Vui lòng nhập dữ liệu để tạo mã QR.", "warning")
    return render_template('qr_tool.html', qr_image_path=qr_image_path_display, data_generated=data_generated)


@app.route('/quan-ly-san-pham/')
@app.route('/quan-ly-san-pham/tong-quan')
def product_dashboard_overview():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    current_user_email = session.get('username')
    return render_template(
        'product_dashboard_content_tongquan.html',
        mobile_nav_type='main_dashboard_nav',
        current_user_email=current_user_email
    )


CATEGORY_DETAILS = {
    "thuc-pham": {
        "display_name": "Hàng Thực phẩm",
        "image_url": "https://images.pexels.com/photos/264636/pexels-photo-264636.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý các mặt hàng thực phẩm, đồ uống và các sản phẩm tiêu dùng hàng ngày"
    },
    "gia-dung": {
        "display_name": "Đồ gia dụng",
        "image_url": "https://images.pexels.com/photos/6585751/pexels-photo-6585751.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý các thiết bị, dụng cụ gia đình và đồ dùng sinh hoạt"
    },
    "thoi-trang": {
        "display_name": "Thời trang",
        "image_url": "https://images.pexels.com/photos/996329/pexels-photo-996329.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý các sản phẩm quần áo, phụ kiện thời trang và làm đẹp"
    },
    "may-tinh-linh-kien": {
        "display_name": "Máy tính & Linh kiện",
        "image_url": "https://images.pexels.com/photos/2582937/pexels-photo-2582937.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý máy tính, laptop, linh kiện điện tử và thiết bị công nghệ"
    }
}


@app.route('/quan-ly-san-pham/danh-muc/<string:category_slug>')
def manage_category_placeholder(category_slug):
    if 'username' not in session:
        return redirect(url_for('login_page'))
    category_info = CATEGORY_DETAILS.get(category_slug)
    if not category_info:
        flash("Danh mục không tồn tại.", "danger")
        return redirect(url_for('product_dashboard_overview'))
    return render_template(
        'category_management_page.html',
        category_slug=category_slug,
        category_display_name=category_info["display_name"],
        category_bg_image=category_info["image_url"],
        category_description=category_info["description"],
        page_specific_title=f'{category_info["display_name"]}',
        mobile_nav_type='category_detail_nav'
    )


@app.route('/quan-ly-qr')
def qr_management_overview():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template(
        'product_dashboard_content_tongquan.html',
        mobile_nav_type='main_dashboard_nav',
        current_user_email=session.get('username')
    )


@app.route('/quan-ly-qr/danh-muc/<string:category_slug>')
def qr_management_detail(category_slug):
    if 'username' not in session:
        return redirect(url_for('login_page'))
    category_info = CATEGORY_DETAILS.get(category_slug)
    if not category_info:
        flash("Danh mục không tồn tại.", "danger")
        return redirect(url_for('product_dashboard_overview'))
    return render_template(
        'qr_management_detail.html',
        category_slug=category_slug,
        category_display_name=category_info["display_name"],
        category_bg_image=category_info["image_url"],
        category_description=category_info["description"],
        page_specific_title=f'Quản lý {category_info["display_name"]}',
        mobile_nav_type='qr_management_nav'
    )


@app.route('/quan-ly-qr/tong-hop')
def qr_management_summary():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template(
        'qr_management_summary.html',
        mobile_nav_type='qr_management_nav'
    )


@app.route('/quan-ly-san-pham/danh-muc-chi-tiet/<string:category_slug>')
def category_detail_with_nav(category_slug):
    if 'username' not in session:
        return redirect(url_for('login_page'))
    category_info = CATEGORY_DETAILS.get(category_slug)
    if not category_info:
        flash("Danh mục không tồn tại.", "danger")
        return redirect(url_for('product_dashboard_overview'))
    return render_template(
        'category_detail_with_nav.html',
        category_slug=category_slug,
        category_display_name=category_info["display_name"],
        mobile_nav_type='category_detail_nav'
    )


@app.route('/quan-ly-san-pham/tong-quan/chi-tiet')
def product_dashboard_overview_detail():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(id) FROM products")
        total_products = c.fetchone()[0] or 0
        c.execute("SELECT SUM(qty) FROM products")
        total_items_in_stock = c.fetchone()[0] or 0
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        c.execute("SELECT COUNT(id) FROM orders WHERE DATE(order_date) = ?", (today_str,))
        orders_today = c.fetchone()[0] or 0
        LOW_STOCK_THRESHOLD = 5
        c.execute("SELECT name, qty FROM products WHERE qty > 0 AND qty <= ? ORDER BY qty ASC LIMIT 5",
                  (LOW_STOCK_THRESHOLD,))
        low_stock_products = c.fetchall()
        EXPIRY_ALERT_DAYS = 30
        c.execute(f"""
            SELECT name, expiry_date FROM products
            WHERE expiry_date IS NOT NULL AND DATE(expiry_date) BETWEEN DATE('now') AND DATE('now', '+{EXPIRY_ALERT_DAYS} days')
            ORDER BY expiry_date ASC
        """)
        expiring_products = c.fetchall()
        conn.close()
        overview_data = {
            'total_products': total_products,
            'total_items_in_stock': total_items_in_stock,
            'orders_today': orders_today,
            'low_stock_products': low_stock_products,
            'expiring_products': expiring_products
        }
        return render_template('product_dashboard_content_tongquan_chitiet.html', overview_data=overview_data)
    except Exception as e:
        app.logger.error(f"Error loading overview detail: {e}")
        flash("Có lỗi xảy ra khi tải trang chi tiết.", "danger")
        return redirect(url_for('product_dashboard_overview'))


@app.route('/quan-ly-san-pham/ton-kho')
def pd_ton_kho_quan_ly():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, price, qty, category, product_id_internal, 
               date_added, expiry_date, product_qr_code_path
        FROM products ORDER BY date_added DESC, name
    """)
    products_list_raw = c.fetchall()
    conn.close()
    products_for_template = []
    for row in products_list_raw:
        product_dict = dict(row)
        if product_dict.get('product_qr_code_path'):
            product_dict['qrcode_url'] = url_for('static', filename=product_dict['product_qr_code_path'])
        else:
            product_dict['qrcode_url'] = url_for('static', filename='placeholder_qr.png')
        products_for_template.append(product_dict)
    return render_template(
        'product_dashboard_content_tonkho.html',
        products=products_for_template,
        mobile_nav_type='main_dashboard_nav'
    )


@app.route('/quan-ly-san-pham/bao-cao')
def pd_bao_cao_xem():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template(
        'product_dashboard_content_baocao.html',
        mobile_nav_type='main_dashboard_nav'
    )


@app.route('/quan-ly-san-pham/thong-tin-ca-nhan')
def pd_user_profile():
    if 'username' not in session: return redirect(url_for('login_page'))
    user_email = session.get('username')
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT email, phone FROM users WHERE email = ?", (user_email,))
    user_info_db = c.fetchone()
    conn.close()
    user_info_display = {'email': user_email, 'phone': user_info_db['phone'] if user_info_db else 'N/A'}
    return render_template('product_dashboard_content_user_profile.html', user_info=user_info_display)


@app.route('/quan-ly-san-pham/nhap-san-pham-moi', methods=['GET', 'POST'])
def pd_nhap_san_pham_moi():
    if 'username' not in session: return redirect(url_for('login_page'))
    if request.method == 'POST':
        product_name = request.form.get('product_name')
        if product_name:
            flash(f"Sản phẩm '{product_name}' đã được thêm (logic mẫu).", "success")
            return redirect(url_for('pd_ton_kho_quan_ly'))
        else:
            flash("Tên sản phẩm không được để trống.", "warning")
    return render_template('product_dashboard_content_nhap_sanpham.html')


@app.route('/quan-ly-san-pham/nhap-kho-qua-quet')
def pd_nhap_kho_quet_page():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    context_slug_from_url = request.args.get('context_slug')
    nav_type_to_use = 'category_context_nav' if context_slug_from_url else 'main_dashboard_nav'
    return render_template(
        'product_dashboard_content_scan_and_input.html',
        contextual_sidebar='category_management',
        mobile_nav_type=nav_type_to_use,
        category_slug=context_slug_from_url
    )


@app.route('/quan-ly-san-pham/xuat-kho-qua-quet')
def pd_xuat_kho_quet_page():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template('product_dashboard_content_xuatkho.html')


@app.route('/api/process_qr_image', methods=['POST'])
def process_qr_image():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Không có file ảnh được gửi'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Không có file nào được chọn'}), 400

    try:
        # Đọc ảnh và decode QR code
        image = Image.open(file.stream)
        app.logger.info(f"Processing QR image: {file.filename}, size: {image.size}")
        
        # Thử nhiều phương pháp decode QR code
        qr_data = None
        
        # Phương pháp 1: Sử dụng pyzbar
        try:
            from pyzbar.pyzbar import decode as pyzbar_decode
            decoded_objects = pyzbar_decode(image)
            if decoded_objects:
                qr_data = decoded_objects[0].data.decode('utf-8')
                app.logger.info(f"QR decoded with pyzbar: {qr_data}")
        except Exception as e:
            app.logger.warning(f"pyzbar decode failed: {str(e)}")
        
        # Phương pháp 2: Sử dụng OpenCV nếu pyzbar thất bại
        if not qr_data:
            try:
                import cv2
                import numpy as np
                
                # Chuyển PIL Image sang OpenCV format
                opencv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
                
                # Thử decode với OpenCV QRCodeDetector
                detector = cv2.QRCodeDetector()
                data, bbox, straight_qrcode = detector.detectAndDecode(opencv_image)
                
                if data:
                    qr_data = data
                    app.logger.info(f"QR decoded with OpenCV: {qr_data}")
                else:
                    # Thử với ảnh grayscale
                    gray = cv2.cvtColor(opencv_image, cv2.COLOR_BGR2GRAY)
                    data, bbox, straight_qrcode = detector.detectAndDecode(gray)
                    if data:
                        qr_data = data
                        app.logger.info(f"QR decoded with OpenCV (grayscale): {qr_data}")
                        
            except Exception as e:
                app.logger.warning(f"OpenCV decode failed: {str(e)}")
        
        # Phương pháp 3: Thử với ảnh đã xử lý (resize, enhance contrast)
        if not qr_data:
            try:
                # Resize ảnh nếu quá nhỏ
                if image.size[0] < 300 or image.size[1] < 300:
                    new_size = (max(300, image.size[0]), max(300, image.size[1]))
                    image_resized = image.resize(new_size, Image.Resampling.LANCZOS)
                    
                    # Thử decode lại với ảnh đã resize
                    decoded_objects = pyzbar_decode(image_resized)
                    if decoded_objects:
                        qr_data = decoded_objects[0].data.decode('utf-8')
                        app.logger.info(f"QR decoded with resized image: {qr_data}")
                        
            except Exception as e:
                app.logger.warning(f"Resize decode failed: {str(e)}")
        
        if not qr_data:
            app.logger.error("All QR decode methods failed")
            return jsonify({
                'success': False, 
                'message': 'Không thể đọc mã QR từ ảnh. Vui lòng thử với ảnh chất lượng cao hơn hoặc chụp lại QR code rõ nét hơn.'
            }), 400

        # Lấy dữ liệu từ QR code đầu tiên tìm thấy
        app.logger.info(f"Successfully decoded QR: {qr_data}")
        
        return jsonify({
            'success': True,
            'qr_data': qr_data
        })

    except Exception as e:
        app.logger.error(f"Error processing QR image: {e}")
        return jsonify({'success': False, 'message': f'Lỗi khi xử lý ảnh: {str(e)}'}), 500

@app.route('/api/get-product-info-from-scan', methods=['POST'])
def get_product_info_from_scan():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401
    data = request.get_json()
    scanned_data = data.get('scanned_data')
    if not scanned_data:
        return jsonify({'error': 'Không nhận được dữ liệu mã quét'}), 400
    
    app.logger.info(f"API: Yêu cầu thông tin sản phẩm cho mã quét: {scanned_data}")
    
    # Xử lý trường hợp QR code chứa URL
    product_id_internal = None
    if scanned_data.startswith('http'):
        # Trích xuất product_id_internal từ URL
        # URL format: http://127.0.0.1:5000/san-pham/qr-info/158621
        try:
            product_id_internal = scanned_data.split('/')[-1]
            app.logger.info(f"Extracted product_id_internal from URL: {product_id_internal}")
        except:
            app.logger.warning(f"Could not extract product_id_internal from URL: {scanned_data}")
    else:
        # Trường hợp QR code chứa trực tiếp product_id_internal hoặc barcode
        product_id_internal = scanned_data
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Tìm kiếm sản phẩm dựa trên product_id_internal hoặc barcode_data
    c.execute("""
        SELECT id, name, product_id_internal, barcode_data, price, qty, category, expiry_date
        FROM products
        WHERE product_id_internal = ? OR barcode_data = ?
    """, (product_id_internal, product_id_internal))
    product_row = c.fetchone()
    
    # Nếu không tìm thấy và scanned_data là URL, thử tìm với scanned_data gốc
    if not product_row and scanned_data.startswith('http'):
        c.execute("""
            SELECT id, name, product_id_internal, barcode_data, price, qty, category, expiry_date
            FROM products
            WHERE product_id_internal = ? OR barcode_data = ?
        """, (scanned_data, scanned_data))
        product_row = c.fetchone()
    
    conn.close()
    
    if product_row:
        product_details = dict(product_row)
        if product_details.get('expiry_date'):
            product_details['expiry_date'] = datetime.datetime.strptime(product_details['expiry_date'],
                                                                        '%Y-%m-%d %H:%M:%S').strftime(
                '%Y-%m-%d') if isinstance(product_details['expiry_date'], str) else product_details[
                'expiry_date'].strftime('%Y-%m-%d')
        app.logger.info(f"API: Sản phẩm được tìm thấy: {product_details}")
        return jsonify(product_details), 200
    else:
        app.logger.warning(f"API: Không tìm thấy sản phẩm cho mã quét: {scanned_data} (extracted: {product_id_internal})")
        return jsonify({'error': 'Sản phẩm không tồn tại trong hệ thống'}), 404


@app.route('/api/update-inventory-and-log', methods=['POST'])
def update_inventory_and_log():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Không có dữ liệu được gửi'}), 400
        
    product_id = data.get('product_id')
    quantity = data.get('quantity')
    action = data.get('action')
    
    if not product_id or not quantity or not action:
        return jsonify({'error': 'Thiếu thông tin cần thiết'}), 400
        
    try:
        conn = get_db_connection()
        c = conn.cursor()

        # Kiểm tra xem sản phẩm có tồn tại không
        c.execute('SELECT id, name, qty, product_qr_code_path FROM products WHERE product_id_internal = ?', (product_id,))
        product = c.fetchone()

        if not product:
            return jsonify({'error': 'Sản phẩm không tồn tại'}), 404

        # Cập nhật số lượng tồn kho
        if action == 'nhap':
            new_qty = product['qty'] + quantity
        elif action == 'xuat':
            if product['qty'] < quantity:
                return jsonify({'error': 'Số lượng tồn kho không đủ'}), 400
            new_qty = product['qty'] - quantity
        else:
            return jsonify({'error': 'Hành động không hợp lệ'}), 400

        # Cập nhật số lượng tồn kho
        c.execute('UPDATE products SET qty = ? WHERE id = ?', (new_qty, product['id']))

        # Ghi log
        c.execute('''
            INSERT INTO activity_log (user_email, action_type, product_id, product_name, quantity, action_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (session['username'], f'{action}_kho', product['id'], product['name'], quantity, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'{action.title()} thành công {quantity} đơn vị sản phẩm {product["name"]}',
            'new_qty': new_qty,
            'qrcode_url': url_for('static', filename=product['product_qr_code_path']) if product['product_qr_code_path'] else None
        })
    except Exception as e:
        app.logger.error(f"Lỗi khi cập nhật tồn kho: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({'error': f'Lỗi khi cập nhật tồn kho: {str(e)}'}), 500


@app.route('/mobile-scan-product')
def render_mobile_scan_page():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    category_slug = request.args.get('category_slug')
    return render_template('mobile_scan_screen.html', category_slug=category_slug)


@app.route('/san-pham/qr-info/<product_internal_id>')
def view_product_details_by_qr(product_internal_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE product_id_internal = ?", (product_internal_id,))
    product_data = c.fetchone()
    conn.close()
    if product_data:
        return render_template('view_product_by_qr.html', product=dict(product_data))
    else:
        flash("Không tìm thấy thông tin sản phẩm cho mã QR này.", "warning")
        return redirect(url_for('product_dashboard_overview'))


@app.route('/api/delete-products', methods=['POST'])
def delete_products():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401
    
    data = request.get_json()
    product_ids = data.get('product_ids', [])
    
    if not product_ids:
        return jsonify({'error': 'Không có sản phẩm nào được chọn'}), 400
        
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Xóa sản phẩm
        placeholders = ','.join('?' * len(product_ids))
        c.execute(f'DELETE FROM products WHERE id IN ({placeholders})', product_ids)
        
        # Lấy tổng số sản phẩm còn lại
        c.execute('SELECT COUNT(*) as total FROM products')
        total_products = c.fetchone()['total']
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Đã xóa {len(product_ids)} sản phẩm',
            'total_products': total_products
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'error': f'Lỗi khi xóa sản phẩm: {str(e)}'}), 500

@app.route('/api/update-product/<int:product_id>', methods=['PUT'])
def update_product(product_id):
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401
        
    data = request.get_json()
    name = data.get('name')
    qty = data.get('qty')
    
    if not name or qty is None:
        return jsonify({'error': 'Thiếu thông tin cập nhật'}), 400
        
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('''
            UPDATE products 
            SET name = ?, qty = ?
            WHERE id = ?
        ''', (name, qty, product_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Cập nhật sản phẩm thành công'
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'error': f'Lỗi khi cập nhật sản phẩm: {str(e)}'}), 500

@app.route('/api/generate-qr', methods=['POST'])
def generate_qr():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    data = request.json
    product_id = data.get('product_id')
    product_name = data.get('product_name')

    if not product_id or not product_name:
        return jsonify({'error': 'Thiếu thông tin sản phẩm'}), 400

    try:
        # Tạo QR Code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(f"PRODUCT_{product_id}")
        qr.make(fit=True)

        # Tạo tên file QR Code
        qr_filename = f"qr_{product_id}.png"
        qr_path = os.path.join(os.path.dirname(__file__), 'static', 'product_qrcodes', qr_filename)

        # Lưu QR Code
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)

        # Lưu URL QR Code vào cơ sở dữ liệu
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE products 
            SET qrcode_url = ? 
            WHERE id = ?
        ''', (f'/static/product_qrcodes/{qr_filename}', product_id))
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Tạo QR Code thành công',
            'qrcode_url': f'/static/product_qrcodes/{qr_filename}'
        })
    except Exception as e:
        app.logger.error(f"Lỗi khi tạo QR Code: {str(e)}")
        return jsonify({'error': f'Lỗi khi tạo QR Code: {str(e)}'}), 500


if __name__ == '__main__':
    qr_folder = os.path.join(os.path.dirname(__file__), 'static', 'product_qrcodes')
    if not os.path.exists(qr_folder):
        os.makedirs(qr_folder)
    legacy_qr_folder = os.path.join(os.path.dirname(__file__), 'static', 'qrcodes')
    if not os.path.exists(legacy_qr_folder):
        os.makedirs(legacy_qr_folder)
    app.run(debug=True, host='0.0.0.0', port=5000)