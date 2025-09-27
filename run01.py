from flask import Flask, render_template, request, send_file, redirect, url_for
import msal
import requests
import json
import os
import csv
from datetime import datetime

app = Flask(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))      # folder of run01.py
DATA_DIR   = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

DATA_FILE = os.path.join(DATA_DIR, "requests.json")

def load_requests():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # if file is corrupted, try the most recent backup
            backups = sorted(
                (b for b in os.listdir(BACKUP_DIR) if b.startswith("requests-") and b.endswith(".json")),
                reverse=True
            )
            for b in backups:
                with open(os.path.join(BACKUP_DIR, b), "r", encoding="utf-8") as bf:
                    try:
                        return json.load(bf)
                    except json.JSONDecodeError:
                        continue
            return []

def save_requests(data):
    # 1) Write atomically to a temp file then replace
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)

    # 2) Create a timestamped backup (rotation optional)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"requests-{stamp}.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # 3) (Optional) keep only last N backups
    N = 30
    backups = sorted(
        (os.path.join(BACKUP_DIR, b) for b in os.listdir(BACKUP_DIR) if b.startswith("requests-") and b.endswith(".json")),
        key=os.path.getmtime, reverse=True
    )
    for old in backups[N:]:
        try: os.remove(old)
        except OSError: pass


def load_requests():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, 'r') as f:
        return json.load(f)

def save_requests(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    try:
        # --- Inputs used by BOTH programs ---
        embellishment          = float(request.form.get('embellishment', 0) or 0)
        upgrade_cost           = float(request.form.get('upgrade_cost', 0) or 0)
        program_type           = (request.form.get('program_type', 'PFB') or 'PFB').upper()  # PFB or PFG (radios)
        sell_through_rate      = float(request.form.get('sell_through_rate', 0.7) or 0.7)

        plants_per_pot         = int(float(request.form.get('plants_per_pot', 0) or 0))
        cost_per_plant         = float(request.form.get('cost_per_plant', 0) or 0)
        total_cost_plants      = plants_per_pot * cost_per_plant

        cost_soil_pot_tray     = float(request.form.get('cost_soil_pot_tray', 0) or 0)
        labor_cost             = float(request.form.get('labor_cost', 0) or 0)
        suggested_selling_price= float(request.form.get('suggested_selling_price', 0) or 0)
        suggested_retail       = float(request.form.get('suggested_retail', 0) or 0)

        # Optional separate select (rename your PO/PBS select to "sell_program" in HTML)
        sell_program           = request.form.get('sell_program', '')

        # --- Overhead (only for PFG) ---
        if program_type == 'PFB':
            # Skip overhead for PFB
            overhead_allocation = 0.0
        else:
            crop_time           = float(request.form.get('crop_time', 0) or 0)
            overhead_per_year   = float(request.form.get('overhead_per_year', 0) or 0)
            plants_per_sqft     = float(request.form.get('plants_per_sqft', 0) or 0)
            if plants_per_sqft > 0:
                # Same formula you had: ((overhead_per_year / 52) / plants_per_sqft) * crop_time
                overhead_allocation = ((overhead_per_year / 52.0) / plants_per_sqft) * crop_time
            else:
                overhead_allocation = 0.0  # safe default if pot size not chosen

        # --- Totals ---
        upg_cost = total_cost_plants + cost_soil_pot_tray + labor_cost + overhead_allocation + embellishment + upgrade_cost
        total_direct_cost = total_cost_plants + cost_soil_pot_tray + labor_cost + embellishment + upgrade_cost
        overhead_cost_per_plant = overhead_allocation  # your formula is already per-plant

        # --- Minimum price targets (match front-end) ---
        TARGET_UPG_PM = 0.30  # 30% margin on Selling
        TARGET_HD_PM  = 0.20  # 20% margin on Retail

        def safe_div(a, b): 
            return a / b if b else float("inf")
        
        # Always include sell-through in the denominator, regardless of program_type
        denom = max(sell_through_rate, 0.01) * max(1 - TARGET_UPG_PM, 0.01)
        min_selling_price    = safe_div(upg_cost, denom)
        min_suggested_retail = safe_div(min_selling_price, (1 - TARGET_HD_PM))

        # --- Shares / flags (guard upg_cost) ---
        if upg_cost > 0:
            plant_share    = total_cost_plants   / upg_cost
            soil_pot_share = cost_soil_pot_tray  / upg_cost
            labor_share    = labor_cost          / upg_cost
            overhead_share = overhead_allocation / upg_cost
        else:
            plant_share = soil_pot_share = labor_share = overhead_share = 0.0

        plant_flag    = plant_share    > 0.5
        labor_flag    = labor_share    > 0.3
        overhead_flag = overhead_share > 0.1

        # --- Profit / margins ---
        actual_revenue  = suggested_selling_price * sell_through_rate
        profit_per_pot  = actual_revenue - upg_cost
        profit_percent  = ((profit_per_pot / actual_revenue) * 100) if actual_revenue > 0 else 0.0
        # Force HD retail based on 20% margin
        TARGET_HD_PM = 0.20
        min_suggested_retail = safe_div(suggested_selling_price, (1 - TARGET_HD_PM))
        retail_margin = TARGET_HD_PM * 100

        # --- Recommendation ---
        target_margin     = 30
        borderline_margin = 29.5

        if profit_per_pot <= 0:
            recommendation = "❌ Recheck"
        elif profit_percent >= target_margin:
            recommendation = "✅ Buy"
        elif profit_percent >= borderline_margin:
            recommendation = "⚠ Borderline Profit"
        else:
            recommendation = "❌ Recheck"

        show_form = (profit_per_pot > 0)

        return render_template(
            'result.html',
            # core numbers
            upg_cost=round(upg_cost, 2),
            min_selling_price=round(min_selling_price, 2),
            min_suggested_retail=round(min_suggested_retail, 2),
            overhead_allocation=round(overhead_allocation, 2),
            total_cost_plants=round(total_cost_plants, 2),
            cost_soil_pot_tray=round(cost_soil_pot_tray, 2),
            labor_cost=round(labor_cost, 2),
            profit_per_pot=round(profit_per_pot, 2),
            profit_percent=round(profit_percent, 2),
            suggested_selling_price=round(suggested_selling_price, 2),
            suggested_retail=round(suggested_retail, 2),
            retail_margin=round(retail_margin, 2),
            recommendation=recommendation,
            show_form=show_form,

            # aliases you were already passing
            hd_pm=round(retail_margin, 2),
            upg_pm=round(profit_percent, 2),
            cost_diff=round(profit_per_pot, 2),
            ideal_cost=round(upg_cost, 2),

            # program info
            program_type=program_type,      # PFB or PFG (from radios)
            sell_program=sell_program,      # PO / PBS (from select, rename in HTML)
            sell_through_rate=sell_through_rate,

            # NEW: for approvals
            total_direct_cost=round(total_direct_cost, 2),
            overhead_cost_per_plant=round(overhead_cost_per_plant, 2),

            # echo raw inputs so we can store/show later
            plants_per_pot=plants_per_pot,
            cost_per_plant=round(cost_per_plant, 2),
            crop_time=request.form.get('crop_time', ''),
            overhead_per_year=request.form.get('overhead_per_year', ''),
            plants_per_sqft=request.form.get('plants_per_sqft', ''),
            pot_size=request.form.get('pot_size', ''),

            # flags
            plant_flag=plant_flag,
            labor_flag=labor_flag,
            overhead_flag=overhead_flag
        )

    except Exception as e:
        return f"Error in calculation: {e}"

@app.route('/submit', methods=['POST'])
def submit():
    print("DEBUG PROGRAM TYPE:", request.form.get("program_type"))

    item = {
        'item_name': request.form['item_name'],
        'requested_by': request.form['requested_by'],
        'bc_id': request.form['bc_id'],
        'sku': request.form['sku'],
        'upc': request.form['upc'],
        'upg_cost': request.form['upg_cost'],
        'overhead_allocation': request.form['overhead_allocation'],
        'profit_per_pot': request.form['profit_per_pot'],
        'profit_percent': request.form['profit_percent'],
        'suggested_selling_price': request.form['suggested_selling_price'],
        'suggested_retail': request.form['suggested_retail'],
        'retail_margin': request.form['retail_margin'],
        'recommendation': request.form['recommendation'],

        # Keep storing the PFB/PFG choice under 'program_type' (back-compat)
        'program_type': request.form.get('program_type', ''),

        # If you include PO/PBS in the submit form, also capture it:
        'sell_program': request.form.get('sell_program', ''),
        'sell_through_rate': request.form.get('sell_through_rate', ''),

        'status': 'Pending',
        'submitted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'embellishment': request.form['embellishment'],
        'upgrade_cost': request.form['upgrade_cost'],

        # Indirect
        'overhead_per_year': request.form.get('overhead_per_year', ''),
        'pot_size': request.form.get('pot_size', ''),
        'plants_per_sqft': request.form.get('plants_per_sqft', ''),
        'crop_time': request.form.get('crop_time', ''),
        'overhead_cost_per_plant': request.form.get('overhead_cost_per_plant', ''),

        # Direct
        'plants_per_pot': request.form.get('plants_per_pot', ''),
        'cost_per_plant': request.form.get('cost_per_plant', ''),
        'cost_soil_pot_tray': request.form.get('cost_soil_pot_tray', ''),
        'labor_cost': request.form.get('labor_cost', ''),
        'embellishment': request.form.get('embellishment', ''),
        'upgrade_cost': request.form.get('upgrade_cost', ''),
        'total_direct_cost': request.form.get('total_direct_cost', ''),

        # Minimums
        'min_selling_price': request.form.get('min_selling_price', ''),
        'min_suggested_retail': request.form.get('min_suggested_retail', ''),

    }

    requests = load_requests()
    requests.append(item)
    save_requests(requests)

    return f"""
    <h2>Approval Request Sent</h2>
    <p>Thanks {item['requested_by']}! Your request for <strong>{item['item_name']}</strong> is now pending approval.</p>
    <a href="/">Back to Start</a>
    """

@app.route('/approvals')
def approvals():
    requests_data = load_requests()
    return render_template('approvals.html', requests=requests_data)

@app.route('/review/<int:index>', methods=['POST'])
def review(index):
    requests_data = load_requests()
    if 0 <= index < len(requests_data):
        action = request.form['action']
        review_note = request.form.get('review_note', '')
        requests_data[index]['status'] = 'Approved' if action == 'approve' else 'Rejected'
        requests_data[index]['reviewed_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        requests_data[index]['review_note'] = review_note
        save_requests(requests_data)
    return redirect(url_for('approvals'))

@app.route('/admin')
def admin():
    requests_data = load_requests()
    return render_template('admin.html', requests=requests_data)

@app.route('/status')
def status():
    requests_data = load_requests()
    return render_template('status.html', requests=requests_data)

@app.route('/export')
def export():
    requests_data = load_requests()
    if not requests_data:
        return "No data to export."
    # union of keys across all rows
    fieldnames = sorted({k for row in requests_data for k in row.keys()})
    csv_file = 'exported_requests.csv'
    with open(csv_file, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(requests_data)
    return send_file(csv_file, as_attachment=True)

@app.route('/delete-all')
def delete_all():
    save_requests([])
    return redirect(url_for('admin'))

@app.route('/delete/<int:index>')
def delete(index):
    requests_data = load_requests()
    if 0 <= index < len(requests_data):
        del requests_data[index]
        save_requests(requests_data)
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(host='192.168.0.228', port=5000, debug=True)