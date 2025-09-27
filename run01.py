from flask import Flask, render_template, request, send_file, redirect, url_for
import json
import os
import csv
from datetime import datetime
from decimal import Decimal, ROUND_CEILING

app = Flask(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

DATA_FILE = os.path.join(DATA_DIR, "requests.json")

# -------- helpers --------
def ceil_cents(x: float) -> float:
    d = Decimal(str(x)) * Decimal(100)
    return float((d.to_integral_value(rounding=ROUND_CEILING)) / Decimal(100))

# -------- Robust load/save with backups --------
def load_requests():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
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
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"requests-{stamp}.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    N = 30
    backups = sorted(
        (os.path.join(BACKUP_DIR, b) for b in os.listdir(BACKUP_DIR) if b.startswith("requests-") and b.endswith(".json")),
        key=os.path.getmtime, reverse=True
    )
    for old in backups[N:]:
        try:
            os.remove(old)
        except OSError:
            pass

# ---------------------------- Routes ----------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    try:
        # --- Inputs used by BOTH programs ---
        embellishment           = float(request.form.get('embellishment', 0) or 0)
        upgrade_cost            = float(request.form.get('upgrade_cost', 0) or 0)
        program_type            = (request.form.get('program_type', 'PFB') or 'PFB').upper()  # PFB or PFG (radios)
        sell_through_rate       = float(request.form.get('sell_through_rate', 0.7) or 0.7)

        plants_per_pot          = int(float(request.form.get('plants_per_pot', 0) or 0))
        cost_per_plant          = float(request.form.get('cost_per_plant', 0) or 0)
        total_cost_plants       = plants_per_pot * cost_per_plant

        cost_soil_pot_tray      = float(request.form.get('cost_soil_pot_tray', 0) or 0)
        labor_cost              = float(request.form.get('labor_cost', 0) or 0)
        suggested_selling_price = float(request.form.get('suggested_selling_price', 0) or 0)
        suggested_retail        = float(request.form.get('suggested_retail', 0) or 0)

        sell_program            = request.form.get('sell_program', '')

        # --- Overhead (only for PFG) ---
        if program_type == 'PFB':
            overhead_allocation = 0.0
            crop_time = 0.0
            overhead_per_year = 0.0
            plants_per_sqft = 0.0
        else:
            crop_time         = float(request.form.get('crop_time', 0) or 0)
            overhead_per_year = float(request.form.get('overhead_per_year', 0) or 0)
            plants_per_sqft   = float(request.form.get('plants_per_sqft', 0) or 0)
            if plants_per_sqft > 0:
                overhead_allocation = ((overhead_per_year / 52.0) / plants_per_sqft) * crop_time
            else:
                overhead_allocation = 0.0

        # --- Totals ---
        upg_cost = total_cost_plants + cost_soil_pot_tray + labor_cost + overhead_allocation + embellishment + upgrade_cost
        total_direct_cost = total_cost_plants + cost_soil_pot_tray + labor_cost + embellishment + upgrade_cost
        overhead_cost_per_plant = overhead_allocation

        # --- Targets ---
        TARGET_UPG_PM = 0.30  # 30% margin on Selling
        TARGET_HD_PM  = 0.20  # 20% margin on Retail

        def safe_div(a, b):
            return a / b if b else float("inf")

        # Minimums (ceil to cents so suggested values actually meet/exceed targets)
        denom = max(sell_through_rate, 0.01) * (1 - TARGET_UPG_PM)
        min_selling_price    = ceil_cents(safe_div(upg_cost, denom))
        min_suggested_retail = ceil_cents(safe_div(min_selling_price, (1 - TARGET_HD_PM)))

        # Required retail for ENTERED selling price (ceil to cents as well)
        required_retail_from_entered = ceil_cents(safe_div(suggested_selling_price, (1 - TARGET_HD_PM)))

        # --- HD metrics ---
        hd_pm = ((suggested_retail - suggested_selling_price) / suggested_retail * 100) if suggested_retail > 0 else 0.0
        meets_hd_margin = round(hd_pm, 2) >= TARGET_HD_PM * 100

        # --- Profit / margins ---
        actual_revenue  = suggested_selling_price * sell_through_rate
        profit_per_pot  = actual_revenue - upg_cost
        profit_percent  = ((profit_per_pot / actual_revenue) * 100) if actual_revenue > 0 else 0.0

        # UPG pass (rounded) — mirror the HD rule
        meets_upg_margin = round(profit_percent, 2) >= TARGET_UPG_PM * 100

        # --- Recommendation ---
        target_margin     = 30
        borderline_margin = 29.5

        if profit_per_pot <= 0:
            recommendation = "❌ Recheck"
        elif round(hd_pm, 2) < TARGET_HD_PM * 100:
            recommendation = "❌ Recheck"
        elif profit_percent >= target_margin:
            recommendation = "✅ Buy"
        elif profit_percent >= borderline_margin:
            recommendation = "⚠ Borderline Profit"
        else:
            recommendation = "❌ Recheck"

        # Users can only submit when BOTH targets pass
        show_form = (meets_upg_margin and meets_hd_margin)

        return render_template(
            'result.html',
            upg_cost=round(upg_cost, 2),
            min_selling_price=round(min_selling_price, 2),
            min_suggested_retail=round(min_suggested_retail, 2),
            meets_hd_margin=meets_hd_margin,
            meets_upg_margin=meets_upg_margin,
            overhead_allocation=round(overhead_allocation, 2),
            embellishment=round(embellishment, 2),
            upgrade_cost=round(upgrade_cost, 2),
            total_cost_plants=round(total_cost_plants, 2),
            cost_soil_pot_tray=round(cost_soil_pot_tray, 2),
            labor_cost=round(labor_cost, 2),
            profit_per_pot=round(profit_per_pot, 2),
            profit_percent=round(profit_percent, 2),
            suggested_selling_price=round(suggested_selling_price, 2),
            suggested_retail=round(suggested_retail, 2),
            hd_pm=round(hd_pm, 2),
            retail_margin=round(hd_pm, 2),
            recommendation=recommendation,
            show_form=show_form,
            losing=(profit_per_pot <= 0),

            upg_pm=round(profit_percent, 2),
            cost_diff=round(profit_per_pot, 2),
            ideal_cost=round(upg_cost, 2),

            program_type=program_type,
            sell_program=sell_program,
            sell_through_rate=sell_through_rate,
            required_retail=round(required_retail_from_entered, 2),

            total_direct_cost=round(total_direct_cost, 2),
            overhead_cost_per_plant=round(overhead_cost_per_plant, 2),

            plants_per_pot=plants_per_pot,
            cost_per_plant=round(cost_per_plant, 2),
            crop_time=request.form.get('crop_time', ''),
            overhead_per_year=request.form.get('overhead_per_year', ''),
            plants_per_sqft=request.form.get('plants_per_sqft', ''),
            pot_size=request.form.get('pot_size', ''),

            plant_flag=(total_cost_plants / upg_cost > 0.5) if upg_cost else False,
            labor_flag=(labor_cost / upg_cost > 0.3) if upg_cost else False,
            overhead_flag=(overhead_allocation / upg_cost > 0.1) if upg_cost else False
        )

    except Exception as e:
        return f"Error in calculation: {e}"

@app.route('/submit', methods=['POST'])
def submit():
    # ------- Server-side guard: block if targets not met -------
    try:
        upg_cost = float(request.form.get('upg_cost', 0) or 0)
        suggested_selling_price = float(request.form.get('suggested_selling_price', 0) or 0)
        suggested_retail = float(request.form.get('suggested_retail', 0) or 0)
        sell_through_rate = float(request.form.get('sell_through_rate', 0.7) or 0.7)

        TARGET_UPG_PM = 30.0
        TARGET_HD_PM  = 20.0

        # recompute from submitted values
        actual_revenue = suggested_selling_price * sell_through_rate
        profit_per_pot = actual_revenue - upg_cost
        profit_percent = ((profit_per_pot / actual_revenue) * 100) if actual_revenue > 0 else -1e9
        hd_pm = ((suggested_retail - suggested_selling_price) / suggested_retail * 100) if suggested_retail > 0 else -1e9

        if round(profit_percent, 2) < TARGET_UPG_PM or round(hd_pm, 2) < TARGET_HD_PM:
            return (
                f"""
                <h2>Submission blocked</h2>
                <p>Requests can only be sent for approval when both targets are met:</p>
                <ul>
                  <li>UPG PM ≥ {TARGET_UPG_PM:.0f}% (yours: {profit_percent:.2f}%)</li>
                  <li>HD PM ≥ {TARGET_HD_PM:.0f}% (yours: {hd_pm:.2f}%)</li>
                </ul>
                <a href="/">Back</a>
                """
            )

    except Exception:
        # If anything goes wrong, be safe and block
        return "<h2>Submission blocked</h2><p>Invalid inputs.</p><a href='/'>Back</a>"

    # ------- Proceed with saving if ok -------
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

        'program_type': request.form.get('program_type', ''),
        'sell_program': request.form.get('sell_program', ''),
        'sell_through_rate': request.form.get('sell_through_rate', ''),

        'status': 'Pending',
        'submitted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),

        'embellishment': request.form.get('embellishment', ''),
        'upgrade_cost': request.form.get('upgrade_cost', ''),
        'overhead_per_year': request.form.get('overhead_per_year', ''),
        'pot_size': request.form.get('pot_size', ''),
        'plants_per_sqft': request.form.get('plants_per_sqft', ''),
        'crop_time': request.form.get('crop_time', ''),
        'overhead_cost_per_plant': request.form.get('overhead_cost_per_plant', ''),

        'plants_per_pot': request.form.get('plants_per_pot', ''),
        'cost_per_plant': request.form.get('cost_per_plant', ''),
        'cost_soil_pot_tray': request.form.get('cost_soil_pot_tray', ''),
        'labor_cost': request.form.get('labor_cost', ''),
        'total_direct_cost': request.form.get('total_direct_cost', ''),

        'min_selling_price': request.form.get('min_selling_price', ''),
        'min_suggested_retail': request.form.get('min_suggested_retail', ''),
    }

    data = load_requests()
    data.append(item)
    save_requests(data)

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
    fieldnames = sorted({k for row in requests_data for k in row.keys()})
    csv_file = os.path.join(BASE_DIR, 'exported_requests.csv')
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