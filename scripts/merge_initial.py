import csv

review_dir = 'reviews/2026-08-11'

# Read existing entries
existing = []
with open(f'{review_dir}/initial_candidates.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        existing.append(row)
print(f'Existing: {len(existing)}')

# 50 general merchants
general = [
    ('KAMBALDA VILLAGE','KAMBALDA VILLAGE','','Groceries','illion','',''),
    ('LAVERTON SUPERMARKE','LAVERTON SUPERMARKE','','Groceries','illion','',''),
    ('SELF SERVICE MARKETS BONDI JUNCTION','SELF SERVICE MARKETS|SELFSERVICE MARKETS','','Groceries','illion','',''),
    ('DORSETT GOLD COAST HOTEL','DORSETT GOLD COAST HOTEL','','Dining Out','illion','',''),
    ('BROKEN HILL MUSICIANS','BROKEN HILL MUSICIANS','','Entertainment','illion','',''),
    ('CECIL HOTEL GOODNA','CECIL HOTEL GOODNA','','Dining Out','illion','',''),
    ('THE DEMO CLUB BROKEN HILL','THE DEMO CLUB BROKEN HILL|DEMO CLUB BROKEN HILL','','Dining Out','illion','',''),
    ('KINGS CREEK HOTEL HASTINGS','KINGS CREEK HOTEL HASTINGS|KINGS CREEK HOTEL','','Dining Out','illion','',''),
    ('PARACHINAR HALAL FOOD MELTON','PARACHINAR HALAL FOOD','','Groceries','illion','',''),
    ('HASTINGS DENTAL','HASTINGS D','','Health','illion','',''),
    ('BACKYARD SUPERMARKET FOOTSCRAY','BACKYARD SUPERMARKET','','Groceries','illion','',''),
    ('NEXT GEN MEMORIAL DR','NEXT GEN MEMORIAL DR','','Gyms and other memberships','illion','',''),
    ('AAA ONE ENTERPRISE PTY','AAA ONE ENTERPRISE PTY','','Groceries','illion','',''),
    ('GOODNA SERVICES CLUB','GOODNA SERVICES CLUB','','Dining Out','illion','',''),
    ('BLUE HIPPO CORP','BLUE HIPPO CORP','','Personal Care','illion','',''),
    ('BOXHILL FRUIT WORLD','BOXHILL FRUIT WORLD','','Groceries','illion','',''),
    ('MA BLEND LAVERTON','MA BLEND LAVERTON','','Dining Out','illion','',''),
    ('LEONORA SUPPLIES','LEONORA SUPPLIES','','Groceries','illion','',''),
    ('TVG GIFT SHOP ANNERLEY','TVG GIFT SHOP','','Groceries','illion','',''),
    ('SPORTING GLOBE KNOX','SPORTING GLOBE KNOX','','Dining Out','illion','',''),
    ('MAMBOURIN FRUIT AND VEGE','MAMBOURIN FRUIT AND VE','','Groceries','illion','',''),
    ('PLUSFITNESS MITCHELL','PLUSFITNESS','','Gyms and other memberships','illion','',''),
    ('GLENGALA HOTEL SUNSHINE','GLENGALA HTL RC|GLENGALA HOTEL','','Dining Out','illion','',''),
    ('SQ THREE HUNGRY BIRDS DERRIMUT','THREE HUNGRY BIR|THREE HUNGRY BIRD','','Dining Out','illion','',''),
    ('WEST END HOTEL TOWNSVILLE','WEST END HOTEL TOWNSV','','Dining Out','illion','',''),
    ('STACKS TULLAWONG PTY LTD','STACKS TULLAWONG PTY L','','Automotive','illion','',''),
    ('BROKEN HILL HOLDINGS','BROKEN HILL HOLDINGS P','','Automotive','illion','',''),
    ('RED BLUEBELLS FDC CHERMSIDE','BLUEBELLSFDC','','Education','illion','',''),
    ('LAVERTON LPO','LAVERTON LPO','','Retail','illion','',''),
    ('ADELAIDE METROCARD','ADELAIDE METROCARD','','Transport','illion','',''),
    ('BWC BRISBANE HAMILTON','BWC BRISBANE','','Transport','illion','',''),
    ('LEO COUNTRY KITCHEN LEONORA','LEOS COUNTRY KITCHEN|LEO COUNTRY KITCHEN','','Dining Out','illion','',''),
    ('THE COOROY HOTEL','THE COOROY HOTEL','','Dining Out','illion','',''),
    ('YUM SING HOUSE MELBOURNE','YUM SING HOUSE','','Dining Out','illion','',''),
    ('NORTH RINGWOOD FOOTY CLUB','NORTH RINGWOOD FOOTY','','Gyms and other memberships','illion','',''),
    ('SQ IPSWICH NETBALL','IPSWICH NETBALL','','Gyms and other memberships','illion','',''),
    ('MARYBOROUGH HIGHLAND SOCIETY','MARYBOROUGH HIGHLAND S','','Dining Out','illion','',''),
    ('TASTY MALATANG BRISBANE','TASTY MALATANG','','Dining Out','illion','',''),
    ('EXPRESSO CARWASH CAFE ARUNDEL','EXPRESSO CARWASH','','Automotive','illion','',''),
    ('THE HARBOUR SECRET','THE HARBOUR SECRET','','Dining Out','illion','',''),
    ('MALVERN MEX','MALVERN MEX PL','','Dining Out','illion','',''),
    ('SQ LABOUR IN VAIN FITZROY','LABOUR IN VAIN','','Dining Out','illion','',''),
    ('MACARTHUR FRESH CAMPBELLTOWN','MACARTHUR FRESHSCORAIL','','Groceries','illion','',''),
    ('C AND T NEWS CABRAMATTA','C AND T NEWS|C & T NEWS','','Retail','illion','',''),
    ('MARYBOROUGH GIANTS FOOTBALL','MARYBOROUGH GIANTS FO','','Gyms and other memberships','illion','',''),
    ('SP USAFOODS MOORABBIN','SP USAFOODS','','Groceries','illion','',''),
    ('KEILOR DOWNS CHRISTIAN CHURCH','KEILOR DOWNS CHRISTIAN CH','','Donations','illion','',''),
    ('SODEXO MT MORGANS LAVERTON','SODEXO MT MORGANS RETA','','Dining Out','illion','',''),
    ('NIB HEALTH INSURANCE','NIB','','Insurance','illion','',''),
    ('DOORDASH MELBOURNE','DOORDASH','','Dining Out','illion','',''),
]

cols = ['merchant_name','keywords','link','category','category_source',
        'keyword_updated_at','category_updated_at','status','hit_count',
        'risk_level','illion_category','samples','notes']
with open(f'{review_dir}/initial_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(cols)
    for row in existing:
        w.writerow([row.get(c,'') for c in cols])
    for row in general:
        w.writerow(list(row) + ['confirm', 0, 'low', row[3], ''])
print(f'Merged: {len(existing)} existing + {len(general)} general = {len(existing)+len(general)} total')
