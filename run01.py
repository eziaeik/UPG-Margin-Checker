from flask import Flask, render_template, request, send_file, redirect, url_for
import msal
import requests
import json
import os
import csv
from datetime import datetime

app = Flask(__name__)

DATA_FILE = 'requests.json'

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
        embellishment = float(request.form['embellishment'])
        upgrade_cost = float(request.form['upgrade_cost'])

        crop_time = float(request.form['crop_time'])
        program_type = request.form['program_type']
        sell_through_rate = float(request.form['sell_through_rate'])

        plants_per_pot = int(request.form['plants_per_pot'])
        cost_per_plant = float(request.form['cost_per_plant'])
        plants_per_pot = int(request.form['plants_per_pot'])
        cost_per_plant = float(request.form['cost_per_plant'])
        total_cost_plants = plants_per_pot * cost_per_plant
        cost_soil_pot_tray = float(request.form['cost_soil_pot_tray'])
        labor_cost = float(request.form['labor_cost'])
        suggested_selling_price = float(request.form['suggested_selling_price'])
        suggested_retail = float(request.form['suggested_retail'])

        overhead_per_year = float(request.form['overhead_per_year'])
        plants_per_sqft = float(request.form['plants_per_sqft'])
        sqft_per_plant = 1 / plants_per_sqft
        
        # Overhead allocation
        overhead_allocation = ((overhead_per_year / 52) / plants_per_sqft) * crop_time

        # Calculate upg cost parts
        actual_revenue = suggested_selling_price * sell_through_rate
        upg_cost = total_cost_plants + cost_soil_pot_tray + labor_cost + overhead_allocation + embellishment + upgrade_cost
        
        # Calculate shares (MOVE THIS AFTER upg_cost is ready)
        plant_share = total_cost_plants / upg_cost
        soil_pot_share = cost_soil_pot_tray / upg_cost
        labor_share = labor_cost / upg_cost
        overhead_share = overhead_allocation / upg_cost
        
        # Set warning flags
        plant_flag = plant_share > 0.5
        labor_flag = labor_share > 0.3
        overhead_flag = overhead_share > 0.1
        
        profit_per_pot = actual_revenue - upg_cost
        profit_percent = (profit_per_pot / actual_revenue) * 100
        retail_margin = ((suggested_retail - suggested_selling_price) / suggested_selling_price) * 100

        target_margin = 30
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
        
        # Send all cost components to the template
        return render_template(
            'result.html',
            upg_cost=round(upg_cost, 2),
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
            hd_pm=round(retail_margin, 2),
            upg_pm=round(profit_percent, 2),
            cost_diff=round(profit_per_pot, 2),
            ideal_cost=round(upg_cost, 2),
            program_type=program_type,
            sell_through_rate=sell_through_rate,
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
        'program_type': request.form.get('program_type', ''),
        'status': 'Pending',
        'submitted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'embellishment': request.form['embellishment'],
        'upgrade_cost': request.form['upgrade_cost']
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
    csv_file = 'exported_requests.csv'
    with open(csv_file, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=requests_data[0].keys())
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