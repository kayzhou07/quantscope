# 量化一 v9Q-R4 平台复核版；20150101—20151231，初始10万元。
# 只用于历史回测。固定便利股票池继承原qrun，不冒充历史全市场。
# 月线12-1；季度5/3只*19%；max(成本,峰值)*88%收盘触发，下一开盘执行。
# 信号由历史前复权价格计算；订单、数量、现金、税费和企业行动由平台处理。
# 本地31.93%不是此平台版本预设结果，不把已有净值或事后选股写入程序。
import math
import pandas as pd

UNIVERSE = """
600000.SH 600004.SH 600006.SH 600008.SH 600009.SH 600010.SH 600011.SH 600012.SH 600015.SH 600016.SH 600017.SH 600018.SH 600019.SH 600021.SH 600022.SH
600023.SH 600025.SH 600026.SH 600027.SH 600028.SH 600029.SH 600030.SH 600031.SH 600032.SH 600033.SH 600036.SH 600037.SH 600038.SH 600039.SH 600048.SH
600050.SH 600055.SH 600056.SH 600057.SH 600058.SH 600059.SH 600060.SH 600061.SH 600062.SH 600063.SH 600064.SH 600066.SH 600072.SH 600075.SH 600085.SH
600089.SH 600094.SH 600095.SH 600098.SH 600100.SH 600104.SH 600105.SH 600109.SH 600111.SH 600113.SH 600114.SH 600115.SH 600116.SH 600118.SH 600120.SH
600123.SH 600126.SH 600129.SH 600131.SH 600132.SH 600133.SH 600141.SH 600143.SH 600150.SH 600151.SH 600153.SH 600155.SH 600157.SH 600158.SH 600160.SH
600161.SH 600163.SH 600166.SH 600167.SH 600170.SH 600171.SH 600176.SH 600177.SH 600179.SH 600183.SH 600184.SH 600185.SH 600186.SH 600188.SH 600196.SH
600201.SH 600206.SH 600208.SH 600210.SH 600211.SH 600216.SH 600219.SH 600221.SH 600226.SH 600233.SH 600246.SH 600248.SH 600252.SH 600256.SH 600258.SH
600259.SH 600266.SH 600267.SH 600268.SH 600269.SH 600273.SH 600276.SH 600282.SH 600285.SH 600292.SH 600295.SH 600298.SH 600299.SH 600301.SH 600305.SH
600309.SH 600312.SH 600315.SH 600316.SH 600320.SH 600323.SH 600325.SH 600328.SH 600329.SH 600330.SH 600331.SH 600332.SH 600335.SH 600338.SH 600339.SH
600345.SH 600346.SH 600348.SH 600350.SH 600352.SH 600353.SH 600361.SH 600362.SH 600366.SH 600369.SH 600372.SH 600373.SH 600377.SH 600378.SH 600380.SH
600388.SH 600389.SH 600390.SH 600391.SH 600392.SH 600395.SH 600398.SH 600399.SH 600403.SH 600406.SH 600409.SH 600415.SH 600416.SH 600420.SH 600422.SH
600426.SH 600428.SH 600435.SH 600436.SH 600438.SH 600446.SH 600452.SH 600456.SH 600458.SH 600459.SH 600460.SH 600461.SH 600475.SH 600478.SH 600480.SH
600482.SH 600483.SH 600486.SH 600489.SH 600490.SH 600497.SH 600498.SH 600499.SH 600502.SH 600507.SH 600508.SH 600509.SH 600511.SH 600515.SH 600516.SH
600517.SH 600519.SH 600521.SH 600522.SH 600528.SH 600529.SH 600531.SH 600535.SH 600536.SH 600546.SH 600547.SH 600549.SH 600550.SH 600552.SH 600556.SH
600557.SH 600559.SH 600562.SH 600563.SH 600566.SH 600570.SH 600572.SH 600575.SH 600577.SH 600578.SH 600582.SH 600583.SH 600584.SH 600585.SH 600587.SH
600588.SH 600595.SH 600596.SH 600597.SH 600598.SH 600600.SH 600601.SH 600602.SH 600606.SH 600612.SH 600618.SH 600619.SH 600621.SH 600623.SH 600629.SH
600633.SH 600635.SH 600637.SH 600639.SH 600640.SH 600641.SH 600642.SH 600643.SH 600645.SH 600649.SH 600651.SH 600654.SH 600655.SH 600657.SH 600660.SH
600662.SH 600663.SH 600664.SH 600667.SH 600674.SH 600675.SH 600682.SH 600685.SH 600686.SH 600688.SH 600690.SH 600699.SH 600704.SH 600707.SH 600710.SH
600711.SH 600717.SH 600718.SH 600726.SH 600728.SH 600729.SH 600737.SH 600739.SH 600740.SH 600741.SH 600744.SH 600746.SH 600750.SH 600751.SH 600754.SH
600757.SH 600760.SH 600761.SH 600763.SH 600764.SH 600765.SH 600771.SH 600773.SH 600776.SH 600782.SH 600783.SH 600787.SH 600789.SH 600795.SH 600801.SH
600803.SH 600808.SH 600809.SH 600812.SH 600816.SH 600820.SH 600821.SH 600827.SH 600835.SH 600845.SH 600848.SH 600850.SH 600859.SH 600862.SH 600863.SH
600864.SH 600866.SH 600867.SH 600869.SH 600871.SH 600872.SH 600873.SH 600875.SH 600877.SH 600879.SH 600882.SH 600884.SH 600885.SH 600886.SH 600887.SH
600888.SH 600893.SH 600900.SH 600901.SH 600905.SH 600906.SH 600908.SH 600909.SH 600916.SH 600918.SH 600919.SH 600926.SH 600927.SH 600928.SH 600929.SH
600930.SH 600933.SH 600938.SH 600941.SH 600956.SH 600958.SH 600961.SH 600963.SH 600967.SH 600968.SH 600970.SH 600971.SH 600977.SH 600985.SH 600986.SH
600988.SH 600989.SH 600993.SH 600995.SH 600996.SH 600997.SH 600998.SH 600999.SH 601000.SH 601001.SH 601003.SH 601006.SH 601009.SH 601012.SH 601016.SH
601018.SH 601019.SH 601020.SH 601021.SH 601022.SH 601033.SH 601038.SH 601058.SH 601066.SH 601068.SH 601069.SH 601077.SH 601083.SH 601088.SH 601089.SH
601096.SH 601098.SH 601099.SH 601100.SH 601101.SH 601106.SH 601107.SH 601108.SH 601111.SH 601112.SH 601117.SH 601118.SH 601126.SH 601127.SH 601128.SH
601136.SH 601137.SH 601138.SH 601139.SH 601155.SH 601156.SH 601162.SH 601166.SH 601169.SH 601179.SH 601186.SH 601187.SH 601200.SH 601208.SH 601211.SH
601212.SH 601216.SH 601222.SH 601225.SH 601228.SH 601229.SH 601233.SH 601236.SH 601238.SH 601288.SH 601298.SH 601311.SH 601318.SH 601319.SH 601326.SH
601328.SH 601333.SH 601336.SH 601360.SH 601369.SH 601375.SH 601377.SH 601390.SH 601398.SH 601399.SH 601456.SH 601519.SH 601528.SH 601555.SH 601567.SH
601568.SH 601577.SH 601595.SH 601598.SH 601600.SH 601601.SH 601606.SH 601607.SH 601608.SH 601609.SH 601611.SH 601615.SH 601618.SH 601619.SH 601628.SH
601633.SH 601636.SH 601658.SH 601665.SH 601666.SH 601668.SH 601669.SH 601677.SH 601678.SH 601688.SH 601689.SH 601696.SH 601698.SH 601699.SH 601702.SH
601717.SH 601727.SH 601728.SH 601766.SH 601777.SH 601778.SH 601788.SH 601799.SH 601800.SH 601801.SH 601808.SH 601816.SH 601818.SH 601825.SH 601838.SH
601857.SH 601858.SH 601860.SH 601865.SH 601866.SH 601868.SH 601869.SH 601872.SH 601877.SH 601878.SH 601880.SH 601881.SH 601882.SH 601888.SH 601890.SH
601898.SH 601899.SH 601900.SH 601901.SH 601908.SH 601916.SH 601918.SH 601919.SH 601921.SH 601928.SH 601939.SH 601949.SH 601952.SH 601958.SH 601963.SH
601965.SH 601966.SH 601969.SH 601975.SH 601985.SH 601988.SH 601990.SH 601991.SH 601995.SH 601997.SH 601998.SH 603000.SH 603005.SH 603009.SH 603013.SH
603014.SH 603019.SH 603025.SH 603026.SH 603027.SH 603033.SH 603039.SH 603043.SH 603049.SH 603055.SH 603057.SH 603063.SH 603072.SH 603077.SH 603083.SH
603087.SH 603092.SH 603093.SH 603099.SH 603100.SH 603103.SH 603108.SH 603118.SH 603119.SH 603124.SH 603127.SH 603129.SH 603132.SH 603156.SH 603160.SH
603162.SH 603166.SH 603169.SH 603171.SH 603175.SH 603179.SH 603193.SH 603194.SH 603202.SH 603218.SH 603219.SH 603220.SH 603225.SH 603227.SH 603233.SH
603236.SH 603248.SH 603256.SH 603259.SH 603260.SH 603267.SH 603279.SH 603283.SH 603288.SH 603290.SH 603296.SH 603297.SH 603298.SH 603299.SH 603300.SH
603301.SH 603305.SH 603306.SH 603308.SH 603317.SH 603319.SH 603323.SH 603327.SH 603328.SH 603337.SH 603338.SH 603341.SH 603345.SH 603355.SH 603358.SH
603360.SH 603369.SH 603376.SH 603379.SH 603392.SH 603393.SH 603395.SH 603402.SH 603406.SH 603418.SH 603444.SH 603456.SH 603477.SH 603486.SH 603496.SH
603501.SH 603505.SH 603508.SH 603516.SH 603529.SH 603530.SH 603533.SH 603556.SH 603565.SH 603567.SH 603568.SH 603583.SH 603588.SH 603589.SH 603596.SH
603599.SH 603605.SH 603606.SH 603612.SH 603613.SH 603619.SH 603638.SH 603650.SH 603658.SH 603659.SH 603662.SH 603663.SH 603667.SH 603678.SH 603680.SH
603688.SH 603690.SH 603693.SH 603698.SH 603699.SH 603707.SH 603713.SH 603728.SH 603730.SH 603733.SH 603737.SH 603766.SH 603786.SH 603799.SH 603806.SH
603809.SH 603816.SH 603826.SH 603833.SH 603858.SH 603866.SH 603871.SH 603876.SH 603881.SH 603883.SH 603885.SH 603887.SH 603888.SH 603893.SH 603899.SH
603915.SH 603919.SH 603920.SH 603927.SH 603929.SH 603939.SH 603950.SH 603979.SH 603983.SH 603986.SH 603992.SH 603993.SH 603997.SH 605011.SH 605020.SH
605090.SH 605099.SH 605111.SH 605116.SH 605117.SH 605118.SH 605123.SH 605196.SH 605198.SH 605296.SH 605333.SH 605358.SH 605376.SH 605499.SH 605507.SH
605555.SH 605589.SH 605599.SH 000001.SZ 000002.SZ 000009.SZ 000012.SZ 000021.SZ 000027.SZ 000028.SZ 000029.SZ 000030.SZ 000032.SZ 000034.SZ 000035.SZ
000039.SZ 000048.SZ 000049.SZ 000050.SZ 000058.SZ 000059.SZ 000060.SZ 000061.SZ 000062.SZ 000063.SZ 000065.SZ 000088.SZ 000089.SZ 000099.SZ 000100.SZ
000155.SZ 000156.SZ 000157.SZ 000166.SZ 000301.SZ 000333.SZ 000338.SZ 000400.SZ 000403.SZ 000408.SZ 000415.SZ 000420.SZ 000422.SZ 000423.SZ 000425.SZ
000429.SZ 000498.SZ 000503.SZ 000506.SZ 000513.SZ 000519.SZ 000528.SZ 000534.SZ 000537.SZ 000538.SZ 000539.SZ 000543.SZ 000550.SZ 000552.SZ 000555.SZ
000559.SZ 000563.SZ 000567.SZ 000568.SZ 000581.SZ 000582.SZ 000591.SZ 000596.SZ 000598.SZ 000600.SZ 000603.SZ 000612.SZ 000617.SZ 000620.SZ 000623.SZ
000625.SZ 000628.SZ 000629.SZ 000630.SZ 000636.SZ 000650.SZ 000651.SZ 000657.SZ 000661.SZ 000672.SZ 000676.SZ 000680.SZ 000681.SZ 000682.SZ 000683.SZ
000685.SZ 000686.SZ 000688.SZ 000690.SZ 000703.SZ 000708.SZ 000709.SZ 000712.SZ 000719.SZ 000723.SZ 000725.SZ 000727.SZ 000728.SZ 000729.SZ 000733.SZ
000737.SZ 000738.SZ 000739.SZ 000750.SZ 000758.SZ 000762.SZ 000766.SZ 000767.SZ 000768.SZ 000776.SZ 000778.SZ 000783.SZ 000785.SZ 000786.SZ 000791.SZ
000792.SZ 000795.SZ 000800.SZ 000801.SZ 000807.SZ 000810.SZ 000811.SZ 000813.SZ 000825.SZ 000828.SZ 000829.SZ 000830.SZ 000831.SZ 000833.SZ 000837.SZ
000848.SZ 000858.SZ 000860.SZ 000875.SZ 000878.SZ 000880.SZ 000883.SZ 000885.SZ 000887.SZ 000893.SZ 000895.SZ 000898.SZ 000899.SZ 000901.SZ 000902.SZ
000913.SZ 000917.SZ 000921.SZ 000923.SZ 000927.SZ 000928.SZ 000930.SZ 000932.SZ 000935.SZ 000937.SZ 000938.SZ 000950.SZ 000951.SZ 000959.SZ 000960.SZ
000962.SZ 000963.SZ 000966.SZ 000967.SZ 000969.SZ 000970.SZ 000973.SZ 000975.SZ 000977.SZ 000983.SZ 000987.SZ 000988.SZ 000990.SZ 000997.SZ 000998.SZ
000999.SZ 001203.SZ 001221.SZ 001227.SZ 001233.SZ 001236.SZ 001267.SZ 001280.SZ 001286.SZ 001287.SZ 001301.SZ 001309.SZ 001337.SZ 001339.SZ 001359.SZ
001369.SZ 001382.SZ 001386.SZ 001389.SZ 001391.SZ 001696.SZ 001896.SZ 001914.SZ 001965.SZ 001979.SZ 002001.SZ 002003.SZ 002004.SZ 002006.SZ 002007.SZ
002008.SZ 002010.SZ 002011.SZ 002015.SZ 002017.SZ 002019.SZ 002020.SZ 002023.SZ 002025.SZ 002027.SZ 002028.SZ 002032.SZ 002036.SZ 002041.SZ 002043.SZ
002044.SZ 002045.SZ 002046.SZ 002048.SZ 002049.SZ 002050.SZ 002051.SZ 002053.SZ 002056.SZ 002060.SZ 002061.SZ 002063.SZ 002064.SZ 002065.SZ 002073.SZ
002074.SZ 002075.SZ 002077.SZ 002078.SZ 002081.SZ 002085.SZ 002091.SZ 002093.SZ 002096.SZ 002097.SZ 002099.SZ 002100.SZ 002101.SZ 002104.SZ 002110.SZ
002120.SZ 002121.SZ 002123.SZ 002125.SZ 002126.SZ 002127.SZ 002130.SZ 002131.SZ 002138.SZ 002139.SZ 002142.SZ 002145.SZ 002149.SZ 002151.SZ 002152.SZ
002153.SZ 002155.SZ 002157.SZ 002158.SZ 002163.SZ 002170.SZ 002171.SZ 002174.SZ 002176.SZ 002179.SZ 002181.SZ 002182.SZ 002183.SZ 002185.SZ 002192.SZ
002194.SZ 002195.SZ 002202.SZ 002203.SZ 002204.SZ 002212.SZ 002215.SZ 002216.SZ 002221.SZ 002222.SZ 002223.SZ 002226.SZ 002230.SZ 002233.SZ 002236.SZ
002237.SZ 002239.SZ 002241.SZ 002243.SZ 002244.SZ 002245.SZ 002246.SZ 002249.SZ 002250.SZ 002251.SZ 002252.SZ 002254.SZ 002258.SZ 002261.SZ 002262.SZ
002265.SZ 002266.SZ 002267.SZ 002268.SZ 002270.SZ 002271.SZ 002273.SZ 002276.SZ 002281.SZ 002287.SZ 002290.SZ 002292.SZ 002294.SZ 002299.SZ 002304.SZ
002311.SZ 002312.SZ 002315.SZ 002317.SZ 002318.SZ 002320.SZ 002324.SZ 002326.SZ 002332.SZ 002335.SZ 002338.SZ 002340.SZ 002345.SZ 002351.SZ 002352.SZ
002353.SZ 002354.SZ 002364.SZ 002368.SZ 002371.SZ 002372.SZ 002373.SZ 002378.SZ 002384.SZ 002389.SZ 002396.SZ 002400.SZ 002402.SZ 002405.SZ 002407.SZ
002408.SZ 002409.SZ 002410.SZ 002414.SZ 002415.SZ 002416.SZ 002422.SZ 002423.SZ 002428.SZ 002429.SZ 002430.SZ 002432.SZ 002434.SZ 002436.SZ 002444.SZ
002456.SZ 002458.SZ 002460.SZ 002461.SZ 002463.SZ 002465.SZ 002466.SZ 002468.SZ 002472.SZ 002475.SZ 002484.SZ 002487.SZ 002488.SZ 002493.SZ 002497.SZ
002498.SZ 002500.SZ 002506.SZ 002507.SZ 002508.SZ 002511.SZ 002516.SZ 002517.SZ 002518.SZ 002531.SZ 002532.SZ 002534.SZ 002536.SZ 002537.SZ 002539.SZ
002541.SZ 002544.SZ 002545.SZ 002549.SZ 002550.SZ 002557.SZ 002558.SZ 002563.SZ 002568.SZ 002572.SZ 002583.SZ 002588.SZ 002594.SZ 002597.SZ 002600.SZ
002601.SZ 002602.SZ 002603.SZ 002605.SZ 002608.SZ 002611.SZ 002617.SZ 002624.SZ 002625.SZ 002626.SZ 002635.SZ 002636.SZ 002639.SZ 002643.SZ 002648.SZ
002651.SZ 002654.SZ 002668.SZ 002670.SZ 002673.SZ 002675.SZ 002681.SZ 002683.SZ 002690.SZ 002698.SZ 002701.SZ 002705.SZ 002706.SZ 002709.SZ 002714.SZ
002716.SZ 002727.SZ 002736.SZ 002737.SZ 002738.SZ 002739.SZ 002745.SZ 002747.SZ 002755.SZ 002756.SZ 002761.SZ 002768.SZ 002773.SZ 002779.SZ 002797.SZ
002807.SZ 002812.SZ 002815.SZ 002821.SZ 002827.SZ 002831.SZ 002832.SZ 002837.SZ 002838.SZ 002839.SZ 002840.SZ 002841.SZ 002847.SZ 002850.SZ 002851.SZ
002859.SZ 002867.SZ 002881.SZ 002882.SZ 002891.SZ 002892.SZ 002895.SZ 002896.SZ 002897.SZ 002901.SZ 002906.SZ 002911.SZ 002913.SZ 002916.SZ 002920.SZ
002922.SZ 002925.SZ 002926.SZ 002928.SZ 002929.SZ 002936.SZ 002938.SZ 002939.SZ 002941.SZ 002945.SZ 002946.SZ 002947.SZ 002948.SZ 002958.SZ 002960.SZ
002961.SZ 002965.SZ 002966.SZ 002967.SZ 002978.SZ 002979.SZ 002984.SZ 002985.SZ 002987.SZ 002993.SZ 003006.SZ 003010.SZ 003021.SZ 003022.SZ 003031.SZ
003035.SZ 003039.SZ 003816.SZ 300001.SZ 300002.SZ 300003.SZ 300007.SZ 300008.SZ 300009.SZ 300012.SZ 300014.SZ 300015.SZ 300017.SZ 300024.SZ 300026.SZ
300031.SZ 300033.SZ 300034.SZ 300035.SZ 300036.SZ 300037.SZ 300045.SZ 300047.SZ 300049.SZ 300054.SZ 300058.SZ 300059.SZ 300065.SZ 300070.SZ 300073.SZ
300075.SZ 300077.SZ 300080.SZ 300083.SZ 300085.SZ 300088.SZ 300098.SZ 300100.SZ 300101.SZ 300102.SZ 300113.SZ 300115.SZ 300119.SZ 300122.SZ 300124.SZ
300127.SZ 300130.SZ 300131.SZ 300133.SZ 300134.SZ 300136.SZ 300140.SZ 300142.SZ 300144.SZ 300146.SZ 300153.SZ 300166.SZ 300170.SZ 300171.SZ 300180.SZ
300181.SZ 300182.SZ 300184.SZ 300185.SZ 300188.SZ 300199.SZ 300207.SZ 300223.SZ 300224.SZ 300229.SZ 300236.SZ 300244.SZ 300251.SZ 300253.SZ 300255.SZ
300257.SZ 300260.SZ 300274.SZ 300276.SZ 300277.SZ 300284.SZ 300285.SZ 300294.SZ 300296.SZ 300298.SZ 300303.SZ 300308.SZ 300315.SZ 300316.SZ 300319.SZ
300322.SZ 300323.SZ 300327.SZ 300337.SZ 300339.SZ 300341.SZ 300342.SZ 300346.SZ 300347.SZ 300348.SZ 300353.SZ 300357.SZ 300360.SZ 300363.SZ 300373.SZ
300376.SZ 300378.SZ 300383.SZ 300390.SZ 300394.SZ 300395.SZ 300398.SZ 300408.SZ 300409.SZ 300413.SZ 300415.SZ 300416.SZ 300418.SZ 300428.SZ 300432.SZ
300433.SZ 300438.SZ 300442.SZ 300443.SZ 300446.SZ 300450.SZ 300454.SZ 300455.SZ 300456.SZ 300457.SZ 300458.SZ 300459.SZ 300468.SZ 300474.SZ 300475.SZ
300476.SZ 300482.SZ 300487.SZ 300492.SZ 300493.SZ 300496.SZ 300498.SZ 300502.SZ 300503.SZ 300525.SZ 300529.SZ 300548.SZ 300558.SZ 300567.SZ 300568.SZ
300570.SZ 300573.SZ 300576.SZ 300579.SZ 300580.SZ 300593.SZ 300595.SZ 300598.SZ 300601.SZ 300602.SZ 300604.SZ 300607.SZ 300613.SZ 300618.SZ 300620.SZ
300623.SZ 300624.SZ 300627.SZ 300628.SZ 300633.SZ 300634.SZ 300638.SZ 300641.SZ 300655.SZ 300657.SZ 300660.SZ 300661.SZ 300666.SZ 300672.SZ 300674.SZ
300676.SZ 300677.SZ 300678.SZ 300679.SZ 300680.SZ 300682.SZ 300684.SZ 300685.SZ 300687.SZ 300693.SZ 300699.SZ 300702.SZ 300718.SZ 300723.SZ 300724.SZ
300725.SZ 300726.SZ 300735.SZ 300738.SZ 300747.SZ 300748.SZ 300750.SZ 300751.SZ 300757.SZ 300759.SZ 300760.SZ 300761.SZ 300762.SZ 300763.SZ 300765.SZ
300768.SZ 300770.SZ 300772.SZ 300773.SZ 300775.SZ 300776.SZ 300777.SZ 300779.SZ 300782.SZ 300783.SZ 300803.SZ 300806.SZ 300809.SZ 300811.SZ 300814.SZ
300820.SZ 300821.SZ 300827.SZ 300832.SZ 300841.SZ 300850.SZ 300855.SZ 300857.SZ 300861.SZ 300866.SZ 300870.SZ 300872.SZ 300888.SZ 300896.SZ 300910.SZ
300913.SZ 300919.SZ 300925.SZ 300926.SZ 300953.SZ 300957.SZ 300969.SZ 300972.SZ 300979.SZ 300990.SZ 300999.SZ 301000.SZ 301005.SZ 301015.SZ 301018.SZ
301029.SZ 301031.SZ 301035.SZ 301039.SZ 301047.SZ 301050.SZ 301061.SZ 301071.SZ 301078.SZ 301087.SZ 301091.SZ 301095.SZ 301101.SZ 301109.SZ 301127.SZ
301153.SZ 301155.SZ 301165.SZ 301171.SZ 301175.SZ 301177.SZ 301196.SZ 301200.SZ 301205.SZ 301207.SZ 301215.SZ 301216.SZ 301217.SZ 301219.SZ 301225.SZ
301236.SZ 301238.SZ 301262.SZ 301263.SZ 301267.SZ 301268.SZ 301269.SZ 301275.SZ 301291.SZ 301293.SZ 301297.SZ 301301.SZ 301308.SZ 301316.SZ 301327.SZ
301339.SZ 301345.SZ 301358.SZ 301363.SZ 301371.SZ 301377.SZ 301381.SZ 301389.SZ 301392.SZ 301396.SZ 301413.SZ 301421.SZ 301458.SZ 301487.SZ 301488.SZ
301489.SZ 301498.SZ 301500.SZ 301510.SZ 301511.SZ 301522.SZ 301526.SZ 301536.SZ 301550.SZ 301551.SZ 301556.SZ 301563.SZ 301565.SZ 301571.SZ 301584.SZ
301589.SZ 301592.SZ 301600.SZ 301602.SZ 301606.SZ 301611.SZ 301622.SZ 301626.SZ 301631.SZ 301632.SZ 301658.SZ 301662.SZ 301665.SZ 301678.SZ
""".split()
FREQUENCY = 'Q'
TRAIL = 0.12
WEIGHT = 0.19


def init(context):
    set_benchmark('000300.SH')
    enable_open_bar()
    set_commission(PerShare(type='stock', cost=0.00025, min_trade_cost=5.0))
    set_slippage(PriceSlippage(0.002))  # 双边合计20bp，每边10bp。
    set_volume_limit(0.10, 0.10)
    context.targets = None
    context.last_targets = []
    context.exit_queue = {}
    context.entries = {}
    context.close_audit = []
    context.audit = {}
    context.order_count = 0
    log.info('V9Q_R4_PLATFORM universe={} quarterly={} next_open stop=max(cost,peak)*0.88; platform raw fills/corporate actions; fixed convenience pool, NOT PIT universe'.format(len(UNIVERSE), FREQUENCY))


def counter(context, key):
    context.audit[key] = context.audit.get(key, 0) + 1


def held_positions(context):
    return {s:p for s,p in context.portfolio.stock_account.positions.items() if p.amount > 0}


def monthly_score(frame, signal_date):
    cutoff = pd.Timestamp(signal_date).normalize()
    work = frame.copy()
    work.index = pd.DatetimeIndex(work.index).normalize()
    work = work.loc[work.index <= cutoff]
    work = work.dropna(subset=['open','high','low','close','volume','turnover'])
    good = (work[['open','high','low','close']] > 0).all(axis=1) & (work['volume'] > 0)
    work = work.loc[good]
    if len(work) < 240 or work.index[-1] != cutoff:
        raise ValueError('short history or no signal day bar')
    if float(work['turnover'].iloc[-20:].mean()) < 1e8:
        raise ValueError('amount')
    if float(work['close'].iloc[-1]) < 2.0:
        raise ValueError('price')
    month = cutoff.to_period('M')
    mc = work.groupby(work.index.to_period('M'))['close'].last()
    p1, p12 = float(mc.loc[month-1]), float(mc.loc[month-12])
    if not math.isfinite(p1+p12) or p1 <= 0 or p12 <= 0:
        raise ValueError('invalid momentum')
    return p1/p12-1.0, float(work['close'].iloc[-1]), work


def before_trading(context):
    context.targets = None
    today = get_datetime().strftime('%Y%m%d')
    days = get_trade_days(end_date=today, count=2)
    if len(days) < 2:
        return
    previous = pd.Timestamp(days[-2])
    review = previous.strftime('%Y%m') != today[:6]
    if FREQUENCY == 'Q':
        review = review and int(today[4:6]) in [1,4,7,10]
    holdings = held_positions(context)
    # Remove already completed exits; blocked orders survive until actually flat.
    for s in list(context.exit_queue):
        if s not in holdings:
            del context.exit_queue[s]
    if not review:
        return
    end = previous.strftime('%Y%m%d')
    a = context.portfolio.stock_account
    equity = float(a.available_cash+a.frozen_cash)
    if holdings:
        marks = get_price(list(holdings), None, end, '1d', ['close'], skip_paused=False, fq=None, bar_count=1, is_panel=False)
        for s,p in holdings.items():
            frame = marks.get(s)
            if frame is None or frame.empty:
                raise ValueError('Missing held mark '+s)
            equity += p.amount*float(frame['close'].iloc[-1])
    market = get_price(['000300.SH'], None, end, '1d', ['close'], skip_paused=False, fq='pre', bar_count=120, is_panel=False)['000300.SH']
    if len(market) != 120:
        raise ValueError('Missing CSI300 warmup')
    risk_on = float(market['close'].iloc[-1]) > float(market['close'].mean())
    n = 5 if risk_on else 3
    cal10 = pd.DatetimeIndex(get_trade_days(end_date=end, count=10)).normalize()
    ranked = []
    rejected = {}
    for start in range(0,len(UNIVERSE),100):
        batch = UNIVERSE[start:start+100]
        history = get_price(batch, '20130101', end, '1d', ['open','close','high','low','volume','turnover'], skip_paused=False, fq='pre', is_panel=False)
        for s in batch:
            frame = history.get(s)
            if frame is None or frame.empty:
                rejected['missing'] = rejected.get('missing',0)+1
                continue
            try:
                score, close, work = monthly_score(frame, previous)
                if len(work.index.intersection(cal10)) < 9:
                    raise ValueError('recent trading')
                if close*100 > equity*.25:
                    raise ValueError('lot budget')
                ranked.append((s,score,close))
            except (ValueError,KeyError,TypeError):
                rejected['ineligible'] = rejected.get('ineligible',0)+1
    ranked.sort(key=lambda x:(-x[1],x[0]))
    rank = {x[0]:i for i,x in enumerate(ranked)}
    old_order = context.last_targets + [s for s in sorted(holdings) if s not in context.last_targets]
    picks = [s for s in old_order if s in holdings and s in rank and rank[s] < n+1][:n]
    for s,score,close in ranked[:n]:
        if len(picks)>=n:
            break
        if s not in picks:
            picks.append(s)
    context.targets = picks
    context.last_targets = list(picks)
    context.lot_cap = equity*.25
    log.info('AUDIT_SELECTION signal={} execute={} on={} n={} equity={} lot_cap={} eligible={} rejected={} picks={} top8={}'.format(end,today,risk_on,n,equity,context.lot_cap,len(ranked),rejected,picks,ranked[:8]))


def can_trade(context, bar, side, s):
    if bar.is_paused or (side == 'buy' and bar.is_st):
        counter(context,'paused_or_st_'+side)
        log.info('BLOCKED {} {} paused_or_st'.format(s,side))
        return False
    opening = float(bar.open)
    if not math.isfinite(opening) or opening<=0:
        counter(context,'invalid_open')
        return False
    limit = float(bar.high_limit if side == 'buy' else bar.low_limit)
    if math.isfinite(limit) and limit > 0 and ((side=='buy' and opening>=limit) or (side=='sell' and opening<=limit)):
        counter(context,'limit_'+side)
        log.info('BLOCKED {} {} open={} limit={}'.format(s,side,opening,limit))
        return False
    return True


def submit(context, symbol, target, reason):
    result = order_target(symbol, int(target))
    context.order_count += 1
    counter(context,'submitted')
    if result is None:
        counter(context,'null_order')
    log.info('AUDIT_ORDER {} {} target={} reason={} order={}'.format(get_datetime().strftime('%Y%m%d'),symbol,target,reason,result))


def handle_bar(context, bar_dict):
    holdings = held_positions(context)
    a = context.portfolio.stock_account
    opening_nav = float(a.available_cash+a.frozen_cash)
    for s,p in holdings.items():
        opening_nav += p.amount*float(bar_dict[s].open) if not bar_dict[s].is_paused else p.market_value
    blocked = set(context.exit_queue)
    for s in sorted(blocked):
        if s not in holdings:
            continue
        p=holdings[s]
        if p.available_amount<=0:
            counter(context,'tplus1_block')
        elif can_trade(context,bar_dict[s],'sell',s):
            submit(context,s,0,'stop signal='+context.exit_queue[s])
    if context.targets is None:
        return
    picks=context.targets
    context.targets=None
    # Exit first; platform confirms actual fills and enforces sellable shares.
    for s,p in holdings.items():
        if s not in picks and s not in blocked:
            context.exit_queue[s]=get_datetime().strftime('%Y%m%d')
            if p.available_amount>0 and can_trade(context,bar_dict[s],'sell',s):
                submit(context,s,0,'leave target')
    for s in picks:
        if s in blocked or s in context.exit_queue:
            counter(context,'stop_priority_no_rebuy')
            continue
        bar=bar_dict[s]
        px=float(bar.open)*1.001
        p=context.portfolio.stock_account.positions.get(s)
        current=int(p.amount) if p is not None else 0
        budget_delta=opening_nav*WEIGHT-current*px
        delta=int(abs(budget_delta)/(px*100))*100
        target=current+delta if budget_delta>0 else current-delta
        if target==0 and current==0 and px*100<=context.lot_cap:
            target=100
        diff=target-current
        if not diff:
            continue
        side='buy' if diff>0 else 'sell'
        if not can_trade(context,bar,side,s):
            continue
        if side=='sell' and (p is None or p.available_amount<=0):
            counter(context,'tplus1_block')
            continue
        if side=='buy':
            cash=float(context.portfolio.stock_account.available_cash)
            while target>current:
                amount=(target-current)*px
                if amount+max(5.,amount*.00025)+amount*.00001 <= cash:
                    break
                target-=100
            if target<=current:
                counter(context,'cash_or_lot_block')
                continue
        submit(context,s,target,'rebalance '+side)


def after_trading(context):
    today=get_datetime().strftime('%Y%m%d')
    holdings=held_positions(context)
    for s in list(context.entries):
        if s not in holdings:
            del context.entries[s]
    for s,p in holdings.items():
        if s not in context.entries:
            context.entries[s]=today
        # Re-fetch the holding period in the current dynamic-adjusted basis.
        # Map its highest adjusted close back to today's actual-price scale.
        history=get_price([s], context.entries[s], today, '1d', ['close'], skip_paused=False, fq='pre', is_panel=False)[s]
        prices=history['close'].dropna()
        if prices.empty or float(prices.iloc[-1])<=0:
            raise ValueError('Missing stop close '+s)
        close=float(p.last_price)
        peak=float(prices.max())/float(prices.iloc[-1])*close
        cost=float(p.cost_basis)
        if cost<=0 or close<=0:
            raise ValueError('Invalid position basis '+s)
        line=max(cost,peak)*(1.-TRAIL)
        if close<=line and s not in context.exit_queue:
            context.exit_queue[s]=today
            log.info('STOP_SIGNAL {} {} close={} cost={} peak={} line={} next_open=True'.format(today,s,close,cost,peak,line))
        log.info('AUDIT_HOLD {} {} qty={} cost={} close={} peak={} line={}'.format(today,s,p.amount,cost,close,peak,line))
    a=context.portfolio.stock_account
    nav=float(a.total_value); cash=float(a.available_cash+a.frozen_cash)
    context.close_audit.append((today,nav,cash))
    log.info('AUDIT_CLOSE {} nav={} cash={} market={} held={}'.format(today,nav,cash,a.market_value,[(s,p.amount) for s,p in holdings.items()]))
    if today=='20151231':
        peak=100000.;dd=0.
        for date,value,unused in context.close_audit:
            peak=max(peak,value);dd=max(dd,1-value/peak)
        exposure=sum(1-c/v for date,v,c in context.close_audit)/len(context.close_audit)
        log.info('AUDIT_FINAL nav={} return={} maxdd={} mean_exposure={} order_attempts={} counters={}'.format(nav,nav/100000-1,dd,exposure,context.order_count,context.audit))

