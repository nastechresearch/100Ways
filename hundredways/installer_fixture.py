"""Fail-closed reconciliation for the reviewed installer acceptance fixture.

The fixture is intentionally stored as reviewed bytes: the installer test is
an acceptance boundary, so partial or independently edited input must remain
untouched for review rather than being heuristically repaired.
"""

from __future__ import annotations

import base64
import hashlib
import zlib
from pathlib import Path

_REL = "tests/scripts/test_fresh_source_install.py"
_INPUT_SHA256 = "ea7a44ad5dfcc22da030f5c5aa9dcd7a71fa2b6a62091d8bf02cd979f3d3f32c"
_OUTPUT_SHA256 = "08d909ce50087a51b2bcc79b7583a9c4559fac0287fbc13a6c6735674f99cb34"
_INPUT_B85 = (
    "c-oyBYjfK+^1FTo4*g&%6{$FJbE!J)oX4@7+&H!;arziV!+}UhLQH}zK+0A<`tP?3fG<gI(#wov5?Czu{h}$Qzpe"
    "6=lekif%aRDGOO~cwjfmnbB{%QMeXj1fB1|SE&0|<&MUje_l_HlU;RTlom+>YT3~pDP%=5g|rD6ps^E{1LOh_U$D"
    "HJc5;t5%(JR{`_mR7kgkF>jHQ?7D3uGT-<J)s9OF9{=mah3aamn8YUOmmiylIt=+(pdawB@}l^@It~=yb^0zf&*!"
    "u7jqWhjRx~dqzRxcHK`;JC6_>os$?lL4u@k=aKm5;9F#(CkjD|eNm5kvROl68Oc|c#N(Tc<=|E&fu1d0EdX<WK{r"
    "W}cvVP9>z_44e5<!StzJb>oM6aq+q|IA3FH|0Lt(%oi_c6}pLM-crlBtDAdGnfQDH2k@7aNq*z>$yzy37@?X>j_E"
    "0LPM-76!p}0Fco_Jd_p6)j>!1E6&pq(Oj)Ta7@=v<U1nsKiP!bT%J8YIX)r2Z)B)>y6{;l7^;$>()>ARO338|lmL"
    "7dU=Uiv4HvJEkI5oe=9nd!xa&b~ie<b6<I^h!+K1!LJPcWb!H4VXTM`<9dQpVJh@xQtnDcbay<vbRCrdqjF&hj9|"
    "FPJEjH$bzNLdNDX4<2vkktbn?(EyEmH3N$bit~$q$4uD$|WBWnhC9e7T-t(*6AGhLpz|e;UM7)Bgv>1apO`^V@eU"
    ")Yt;CTfEBR5=$@%8LSymDtS}F?$VbG`Fq{ks{HxZ$w6&bzekJ0SN2@h(fO`z23L2}{K-K~zvGU#h;4CpON+x5p_^"
    "#i;S#z|oZsz0(<`zVArg_94cwCiip7M@t&51QQgD^*EK!Y#2kY2_L5Bz_G<{G$t5Wym!PF~K2_E;t)DE!+Fy{HeZ"
    "a&Xm<)RG}%LUD;Un?ZJ$2<0ftm}KbV{R2&K@-46SFop~Oe54PrPUwWthu5zncme@}KI{@h7<k-H`;G%PrXd)X9`)"
    "&PdOVvMgl!BA8$!lpL1TNX|L9oU`o{MBN5sDMm$I@1c`X7YT1Yx{%sGP&Z&k%Dqs|QC9B?bny(UToK%O-ws>=id1"
    "09Y?7x4lte?GP(JsOp4>B!*R{U?^N$wPOQeM8jXnGzgGi<0{V#93fequ8RT*u)G%WfW1TJ@jy+1xN=C5rJB!`P`$"
    "=s55M1g#Z=At1M5v=lQ>X_<@LpT@yMo`XxNooIKB8ot*56;Du-T+2s^uxWno2u%sQ-OOUwUWT2kAsLZ_uJ#?7MwK"
    "1EU)7!UTB)JY?MW|d(?a~ZH@%H-tIqbm|9@;3VfL@+n{em`i7JRzB@L%_3POnZs-kzVmjdl(qd4XiQ<nam>Nk9D>"
    "-TZO;_WCM%clGi1^zt%#^X`K?;B0Q#$K0NHbN$=Z<@M>Ck4T2(V2#vbJUcymd)`6R^&O*7I@}tZG2EawJKn{zxd7"
    "|*(O^4J2mDe+F-F8)_jESoJ0RZz(Z+xzgk&@zkIEuLk_-IZu^rFRH8!Bb;9h}gMkRkJy~WXY{~Du$_3E=MAQP1f@"
    "`zAd^7GGxKK2RQ($A7VK&JZf$ItR;1gehn1VVmDt8xK48$Nku$8sAhsqF!yHb~W;7?SVNWRQ2w3wY@OHXPsajb-@"
    "b$IIyK{KIW@@$T{*S#K6lTkwAM?)Rt<)R7Cgki*m(>7?iCcsYO|i5)w}m4Si7tOoUx2T>0)%d-)Q?~}02Qv9Ldam"
    "kbLdFNKh6_B%#>87j+S{#S~eo(gS5Po2V6`1y;yef;TG%*fpFi>9o2*n!gg~zX-1MrH+ci{ztSkNlNB|m#H<SIai"
    "ii5lnHYASfZHS714Fuo5bEP`e<7u~Ph43AD&+fRXRNA<avJIk%^Q=grRv{IZ26bW`fz0EyLenwDrzwdz6p-cykj2"
    "y_YL|`R$t7!i^nIBdU#g!KUkbQ$0}Cb&&0P4)D;4ji#=3;Eo3sPibH~0{S*LPQCW=$heXT2|u3haWA5)K6PZ-s7)"
    "vsSkB2VOumN+~H?y~8Tn4`F4xw{SwMl4QuRyQo$U20PiuJ0#5s=*2h-+>2E;T`7w#ow?#bhd-t(b?rYXn88IQh&i"
    ">Q_q5Z6IWGfFfEl=%at)Par|0>+bYRZ;z4YZWnh-RH@oL@iM1p+4cJS*bg}+BV`a7g7-N0{>xA+d<pfr&ZEbF<0Y"
    "8`NhG@3nzH9J`9J;#AsOf{MB4H&rxff`@XQFghU|VIF*h6QjOgMuj$$v8Y3M=}2VfV%W&Nk4Tfye0Fwp&c3m?^N|"
    "TdMrtUBWu8CH8U%YEaZ|oAn{D5j8ctO{H|mgd(UZMHxlT2);mafTCP+>`&YpO1SeF8Tsk{id<G$={#vNAk)aoG_o"
    ">v$7{LZ>sQRkF|%(_{f*(S3`~O#MOa@72XAnoU?l-M2JK$5yJyjuc1L48ZvgEQ?QHihwtb^#ciQM?q@K??3gtL>?"
    "$En7Jgghp0M(+s4tGTc<r$p(0)ae0D2fdUOKgUBQ3DCa)(Q|wTuzJ;BxnKlhADL@=xj>6Js2ctNR$aA0;WoRFR*l"
    "06^J=?QrHvs9yOhUqtWlt)B(*tOH^9eiothb8mbM~5`^>^Uh4kLwJ9O}IeZx$pTN#hgLZM$r9GT>uo~fpA&PeNhy"
    "#frsj@<QwP=RhSr=17V2~F$)M2ULMGdfkHk`umY3q|d_-a+)X2REI6PsT1(8Y{1nV?$3sw?io1*YtSK!Fu3;@sus"
    "5u;G(%x%15Mm{hKl6na_QgWZk4K&3m-D2T3XzQCp1zekf!Q$_1qHS_9mTLB8IJ4!7rLT_X*BJ3kSnABf1myU7eqr"
    "Mu^!sT#A~)~5JqgBGV=D@2XlGJ^!+mC}3B;(ubyGL-umRjA5mNwR994joBoz)<AcpI*Y4|e+RW6s<@$Uu8PLdxZo"
    "QX*X&6m0v(2meYX3}Lk_Rp@|H_%TFym~-4AFls<es&vu>-^?;);o^HC=%@rC!ZxT#k|)s^T#77!K;)XO?GGw>N`f"
    "{2#e683C<hvX?i@FZHL3&Ef5Q$2pu+xuzoy>BD^z-jwbH%&uArw4J*lGRLh7|D#d_lhB$lCBO61Y5l{$}ekPR4Re"
    ")SPp@%MmG*@B?mEi>OWBsT&*c^6q7SEV&vGN2*EyEYXr-&>-JlMc)d#Q3nP(~W#<zp)h=M@t&ZCmEHa0LfxL94X^"
    "GfJ)29d(Xy&rhI+9pkuaL=M0N7<P@+ai;Y^`-dKyo3;T+DAQgikGjb(aO9+KI)B^a1rXAM<zKCK@tS&o3Sp09S7M"
    "ICefJZ99GQw9hm<Ka$Z2>yf`SCZ3_svcb^wEeP{7ger0Pir!h`Qh7Z;=z>twHBK+0H>c=ilXVRA(0Y}?+T!lt-AG"
    "jj)*GC<n(91cv@R6Ag<wQB6Nqz!bwUmu$dT?^z!SUA5rJpUm$3A2W(;kL)!85k^4Fhtk_dBekY{OF_^80pg|(drd"
    "@@xv=P=%(m_U8t2|v-=$9vXI_`6@N$sR9<l9c5AH`I5v|}!*Vl*o%BUk%L4L!%g1bQln2|69+WSAPRTR!>SX_#{`"
    "j}O%aj|g3UY%jlZk-xO5!NV8y*M&n0XHPeb3!B1pADEn2txylms<_+<l9u8}-wrC~b>rfBSR%zpg+Iq`2NGFj4#!"
    "d==1-@qcV_jPk<^cR_y(*b!;yF8>dIV3_X$A=l6$CtZ7?;D(vQoPC903ktgH5v2IqheV<c32)C&-|QT1yr5K~a4M"
    "=(Xuqkh(@D~6tIgb|9@~95zqyV+eYk9fJ-hT_<`2mfY!&R(78!L#u45peY|0zcZ5oe-g4%()t>K<S0n}VUreAeMW"
    "DYm-vkz{VAbqS26nFx-nFTa9I2-gPHz-I)b!W(P-1>u^#r8fuU?bO`rUm=~;<v74>&{Mg$2NGd=CC{O^xLt0J!Ju"
    "3>EYbP(u_<^Cu8P*&G2;pB!+Ag6il1AJ8rG@fnAkz>#>1$UfU1pjtU6Qa!m~}fYUg$ZD;JsZr3txJ$kmw%n^mFmz"
    "+*q2*bi$(<9r=bf#E4Q>(%Mi^84h(EIHQ+2Kh@$&)i{d^3!j*8Xg*VTA1(w0|sc*VOV9tkHgb`eyk~tzN90>+iXM"
    ";BV~3dhSZ3N&g}fW6&pSG{cdre$gi&4PcC_gG<jr3^Ghb%e9rvbnjdj+!RUfj4f;tufUIpDMR<gFBJBZT`iI+bra"
    "kShXwT)v?tumNMy$=?6fMu$_I)gMa&q?e!ywJhj)in8p{sX(XfT-YwVv71U*sr6QK>$ok-ZBYJc!H8$My8Piw*;>"
    "1g4A&>e{5`ZuPs4x6s2%PL)(KZCGkqy3l)9l92-wks<0hXrg6Ne#b~2HmlX%FJxk6{W)iTOGFDK&A8P;M1?s^~J^"
    "IyQ_0smK;+@>uED{PCA`s%p{rP)D3DOoXG{`0?GqBf3}RE{uQt96lLH(%$Oly__a@j<MZEd&p%w9US9s;zdyY?{p"
    "I`(?I38rf1K1`=jMEYe|Hd)j%T`x&g_?J)A?k8s(jXD9LP#Gc$pptY~K`aZnP(GEYUf;LqPL+&>77B^=Ji_x_@F="
    "`C@@XyeF-y+5aDd^Iy^"
)
_OUTPUT_B85 = (
    "c-oy>>vP*S692Bh0*8K(l!{c8xVcmvcFyD2O>P|9lQ?~hqTxU!Bq1h279eG-9{umP3xF?4Zqmz)V-k<W?&7ykno|"
    "1PI!ic-N~O3gh>*HqNy62LD9#dc^Pb#i>W(YIWK5DQf-#ooiHKMsGD%{da~X3PZT(*FcFoBm%L-j6mXjjOl4#9@B"
    "tny1@ti3hlcma1QmkQUo#~>l-7(u@oyl>z`N_@+-IG~C82O8<%(Js3&hBNBv6vKG7e2DaqCZQaxFdoG5~iZH*uW5"
    "MNU|(nu;^~oTa+S+fo!2kDS;`u0#;Nh3z2fz9EqG80Sl0z5ORw$j_^&Ayj&zguYqF1a4%Qd?@>y7BF!^ZkTuimL@"
    "cV;FFKRebEbPn+?*A-gt*}wc&(P`by<j{ek&Kb$|A0HJ+f^-MwwiSRW(pBwG;`jU$ZnpMymIGi+bug7E)gqnc@`>"
    "?!H5yvEqeYgUhuKE5oIDC`#0;TOHo7IZsAJbF~4*F<m{8?}*I)WK(i;dG`EdazZ@M=uq=y>9IsGyh?(X<}El=Law"
    "H82_R<<fY2IFxOhF8kY%RKHp?<`$AjJ!OL+m{(`yE|58E9)3|qb4hwJNG5*URJ!Vr}ahC?4HXUT>ShCV`07JBw#-"
    "s|=LW2yToQ+IxzumVm^^?)WqmJf8eH*b$t;x9g+OI9WY9g*2pCi#faRA>#nct$HQP8T2_;(*SFy_heJCc{d_wMm6"
    "FltRR7Slf<(5kOyf&r}*BSO!I!n}<r}BVu?MPJ0CYl^YP+BFDd9i)cNd<pw0cIR;vV8!I=!))F+a_T2ekED_HOCL"
    "=_A+iqa5*_vB3bNU3h`Qd_T9`Xksl?7WQyro;SV*|z@%ogfX|4Sz1AZ7Ug?0<xC_1!$EV42OPFXuzMEfo?j{M$CY"
    "s5Y%LFx7xmnjvFCafsHFetH)R<v2^3WN72v4YhOftgLo3x(rzONFQFE&?%u0uV01m1PX-vutN;jz~gS#H5|a024`"
    "3ds7Hsh$$V}IHa;+@85xr$jqIu3qZ4uG8SHtFNPXw6WN8^1Gz5@oA?eTw=Kvkts*+nl9SoujXe-VKwU_XLJqsqPi"
    "x?aO9gaww@Dh-}7+aPe&6Vuv$k5#XCsr`Y19y~tL(*WG5)4R-f_pjmSrAmC+QKm3MhskK7*cn8XyN7-ARN?8_-d7"
    "8ivfK`9k2}wVU-`P(<~l5&;I?x4@4~On9!lQU&0g3$@A>h$;p8VUJR@}`;vkV_aq%2*R)&p5;U&2DO}H8SY(4GJ+"
    "_(4je*V0>FrwpNv3@m5h{~2J2VGXyuE&Z4s&qC05J+zKrc_PenCu~`JZktyw_cw)2q{ux94YX!@Z5jo-dg$c(lfg"
    "q@R8bZ~nM_dwmtYyZU&0dU+YXdH2C>aF`o*DK|UbT>o};d42lkBeEemppjaNXQyXx&s&7rwqq`o4tIuT1ShENo^`"
    "QwApo5o0=Bj4h+V2MLXWs_o(@BvTjbeAv<8p_my7@!P?@L5a*n@S*cmv!#%AfzzgGawu;35HVA=ofUt_#rz5Xn7h"
    "(yJbJmRVy`T1u;AG?h0=x50vAX5GK<7e3)!Bt0D3@$&QWwC@i8$Nkt3%NB)Dm<Xq2Cdp1L-IXB24&YghnLpETH-s"
    "twE}<qcp09Zf4B`V-d&!f==A{J7QA1*`#tO~YUu?`$WdxQI_;P`9u7;8#TJfnq-WM)RD*nJ0A3Fw%d-)Q?&F||Qv"
    "4z3QNiQjdFxb&6%eyf=sK<O8yZLeeo(iY0DfSE<rwzEtSs`fFg^})Fu1(x5t22S3ns6h!{RlM?t%*jzM#1b7yRtO"
    "5UYS3UL3@Ypk{GYO@mhiYM}V$oh#Mi9(TJ*tAy{!dv?c7rqcM0gl&;bl%;tBxe6&UHK+pX2y`ALB|^s}pC%>ZkU*"
    "LfKo>I;s9iLICl{>o(eq?xY^i!yd?nz_EesexG<WVVD^;`~8mkn_PSV!GjyZO%${LmPGGUa6_G^_fwQ;o{d`v!OE"
    "n!sgRlR;Ch&&N98s=~tn9H`qVutFH#r`-57_unY8{M*Wf2avXIDQ!Xs0I||o?GrihId^07k?xA&|wFwqqEC*Q1X<"
    "3Qg6v3lg|RaiOZ@qKucxiYHc7UCa)!!t&%(;1Mp3<@XgToX7*gJFqZ_Q0lZ`@=j*>SD6<K`7~>P56VhkY6QEez+}"
    "vaXUM7<*(QL^*SK#FtRCU`?T?dtU%nEK|FYtWNMB$FWvdRc?K+bX*a|T0_|77MBX7t(8&W&L?+d^>$7Nav;ZZVNy"
    "q`-P_rLuc>2y2y=Sj*v3L!v6%tPKrnRZ}xtXG+H)6hclZiZFB__yWNJl5)kdK5=8H;nrhl^ryQca#doc^CV<Ir=i"
    "tpXm#qgS9-zMufWI&a|o#J#Bg5+ra*@ztgD5iC%C165+4nNwh!5#vt&%UqtTz&3+)hXO?M8qb>qO!G~Uf<JzH>8%"
    "EW)}$h$H;s0vvhuSE}9(&Z_nXRz}NIC39XQ7lMUY$Lq$8ptq~RzOJNaBM&jBLeIRGwMjt`HZ$}FbL2PC}Ri&OcnZ"
    "GVCt+&P;=t6aG>r3ZdwUPeZT#gTQvVH@zR1u4W4t;kZrh<AfOX?sp>OVrUdlo;H5t~fth|ycG++99?V))jdFtkRX"
    "cjbhJ+uNX|4yAYKFUcn^Mi7pXJ!pVTso!4QqjHFoWN-#wI<m)iTG)m~YG^7QN=7O&NPKez}2Bm)r$Q4B1B)`Bt$="
    "b6=K6^g@Ay+jtK~9taAWdI>R7a*xR^6vYYMVdB<n@|$A`ToZu-@pm@R*0C5<HTyE0+jPb9SNZd6^mxWCwfbQKVtm"
    "!Vu>KF~{UjNYoA>RS1bwW*ib5Wmo|NxspV@2zDQYm?#C1HZ1GhoMBtYm#6;LH{iOm)8;ku{`{**zM%OzI)2hOq;<"
    "j1(q_@txorLG6Gr}UBObd4ZeG*$a&m-3q(Pc^+di*7z#|M&dtHvHBe&Sc)%jtOZ9CE>l0adxi<!|7*P<!q3_8mA9"
    "gUtbpegMBEl-CzoVLCZuQ`;fku3GYw$<n=4wMx>7kY=4Tq3VfPPrt{r!*f|CKNEjjyVQ9DrLp(DK`%`!LXGGX>P{"
    "vt=S2`l4O3>Mw2GD`$Nyp%%1SAoKmkOma<)ai&++kZlnklh@ta6I<G51s)@QRh7r8A~G%v!<7i{QoZDJFAJ4;H_>"
    "PM94L)R9JC``9SMS=5xwnsU2IdcnAwrRByfGhiq;Ef|b&&Wj=c9b>C&M2@WS(PbO0V?XSHb~ioLC+&RDP>PUg3J2"
    "RY28o>ZP1nHIhdzV@fAv>$yLe31d<B=uv7#~C;k^4PtQ?tY02`bM6xT^G89_1wYK9-MMccf=CMwYAR$*0_1lNPzO"
    "XpLhRqOO1*+9-%91rXs;KIb0*50Nx!V8<>^xX6;T(|*kS3Nv{Y-Kdn)!0r;<0L(=+BP4$^2xQou=jPmcSNv@W`U~"
    "Vw8#Da84OVXB20n4;bAv^blNmF{8UQ{^Q<*{@xv?F=sNJe9jGqDW(EuF=|XDYulYkPAZvpocRPz(kl1upjmUKici"
    "L51r3=Uv8ad_%Z9tfI{Gfj6b4s3(S0{(ZbX&*GSteC+8I)^^nJ5OFmlE4hgW(YufKls+-FM7gL!d|*sA)2)J1lq;"
    "DBZVMy19P35``^K?Qd^^|5w?|k?`2r1rWtw{#O@z5&n<mk-7Zf!X40^0&zqVxWoU$9~j0;U&swq*Kr#!6wEMnl+&"
    "*;Y?s2_br@27<3Rw@hK0B1r*HPQ)>cp`k-ICZ!f&@Aufj>(DalRGr)v3qIKR0LKYh5Y8%6v4Vb&0k8DJG~Y7>+yo"
    "7WLAP}HfBsZ@={LNe}_x~buTLBXnpf-GU(R*~6U>(4&8MuhY*uTWqKU}koq!Qi~t>H44|9oMY}(WJ2l+sEyEw!k8"
    "=+llk}1Nd)U>DKL??zen!V<lm`SL!t_|Efa+w$h=wPo-|RnkvZj4C}`0;ZAhf#wnN*bH7zv-3L}=&aB5~wX@o;OL"
    "ts=bCw%wm;su`m~AO!ceX2@S!2=jePQ|(j$Uy(buJ8(dQJPb5NdV6_Bvj@|CfZ@)8hBrFJw<9AtjH`EcoW=H!c2b"
    "u3?nzio1WTa90rX1kh-|Tz#{CXZBvKo~v)cK;UoiVl8(qlDK=2@iAzV6`A44WzA?45C+gkRnDbjAch!*qSeM~W_G"
    "Zzz1$>8ZjViDk*>fGkO@Qc#jhLob6=&B8Fk&?6dMiI*S9Ck%xGlKDr~1K#>@wjBSlOL&3^o8zNNPtT^iF4R}HbHs"
    "fp~L9{3$qcLSky(=AWf;_9$zHy=I$(Wh_15NX}Q5v98&PU_#7N?T&Of-o(0Vg7`|rj7PPEL8VexZEx;%^xMO4Fom"
    "(#_G3QHYzoJRhN_wk4&}1cFma9XNOO}hSwJtm+!95ZCWy+j@Q$=Umdrq(uhg2z>Xc{LfFsq$pxebws&m>!TVRdI#"
    "U$B`>10Yh{4xBFOJWDzdiqOb$WUEhxh*U>hzcMH?&2d{&sRwec4;^CH~DqKw6gRGCDI~sBP<W1778`I^sZ7vd+uY"
    "IDmbVxVg5Vz?Md9?;Zp7r$q;t!{ZSJmb-rftZcc&2H=yZs^|X){f}|n"
)


def _bytes(encoded: str) -> bytes:
    return zlib.decompress(base64.b85decode(encoded.encode("ascii")))


def _mark_unresolved(unresolved: list[str] | None) -> None:
    if unresolved is not None and _REL not in unresolved:
        unresolved.append(_REL)


def reconcile_installer_fixture(dst: str, unresolved: list[str] | None = None) -> list[str]:
    """Replace only the exact reviewed installer fixture with its reviewed output.

    Unknown, partial, or malformed inputs are deliberately retained and are
    reported through ``unresolved`` when supplied.  The reviewed output is a
    fixed point.
    """
    path = Path(dst) / _REL
    if not path.is_file():
        return []
    current = path.read_bytes()
    digest = hashlib.sha256(current).hexdigest()
    if digest == _OUTPUT_SHA256:
        return []
    if digest != _INPUT_SHA256:
        _mark_unresolved(unresolved)
        return []

    replacement = _bytes(_OUTPUT_B85)
    if hashlib.sha256(replacement).hexdigest() != _OUTPUT_SHA256:
        _mark_unresolved(unresolved)
        return []
    path.write_bytes(replacement)
    return [_REL]
