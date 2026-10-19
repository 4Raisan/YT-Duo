class CSAnswers {

    // 1
    public static int num = 70;

    // 2
    public static String check() {
        if (num>=0 && num<=100) {
            String x = "Green";
            System.out.println(x+" LK");
            return x;
        } else {
            return "Guy";
        }
    }

    // 3
    public static void failed() {
        if (num>=50) {
            System.out.println("Pass");
        } else {
            System.out.println("Fail");
        }
    }

    //4
    public static void four() {
        int cs = 4;
        if (num>=90 && num<101) {
            cs = 1;
        } else if (num>=75) {
            cs =2;
        } else if (num>=60) {
            cs = 3;
        } else if (num>=50) {
            cs = 4;
        } else {
            cs = 5;
        }
        switch (cs) {
            case 1:
                System.out.println("A");
                break;
            case 2:
                System.out.println("B");
                break;
            case 3:
                System.out.println("C");
                break;
            case 4:
                System.out.println("D");
                break;
            default:
                System.out.println("F");
                break;
        }
    }

    public static void main (String[] args) {

        System.out.println(check());
        System.out.println();
        failed();
        four();

    }
}